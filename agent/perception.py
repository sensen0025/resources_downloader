"""感知层 — 把页面压缩成「可交互元素索引」喂给 LLM。

对齐 browser-use 的观察范式:不把整页 HTML 给 LLM,只序列化可交互元素,
编号 [1]..[n],并在 DOM 上打 data-agent-idx 标记(动作层按标记操作)。

要点:
- 原生 JS 做可见性过滤(不用 Playwright 的 :visible 伪类,JS 里不合法);
- 递归穿透 shadow DOM(Claude 新 UI 大量使用 Web Components);
- 每次观察前清除旧标记,重新打标。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .browser import BrowserSession

MAX_ELEMENTS = 80
MAX_BODY_TEXT = 1500

# 可交互元素选择器(原生 CSS,不带 :visible)
SELECTOR = (
    "input, textarea, select, button, a[href], "
    "[role='button'], [role='link'], [contenteditable='true']"
)

_JS_COLLECT = r"""
() => {
  const SELECTOR = %r;
  const MAX = %d;
  const out = [];
  let idx = 0;

  // 清除上一次的标记,重新编号
  document.querySelectorAll('[data-agent-idx]').forEach(el => el.removeAttribute('data-agent-idx'));

  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return false;
    return true;
  }

  function prio(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') return 0;
    if (tag === 'textarea' || tag === 'select') return 1;
    if (tag === 'button' || el.getAttribute('role') === 'button') return 2;
    return 3; // a / link
  }

  function collect(root) {
    let els;
    try { els = root.querySelectorAll(SELECTOR); } catch (e) { els = []; }
    for (const el of els) {
      if (el.hasAttribute('data-agent-idx')) continue;
      if (!isVisible(el)) continue;
      const r = el.getBoundingClientRect();
      const tag = el.tagName.toLowerCase();
      // 输入状态: 密码只暴露"已填/未填", 普通输入暴露值, 勾选暴露状态
      let state = '';
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox' || t === 'radio') state = el.checked ? 'checked=1' : 'checked=0';
        else if (t === 'password') state = el.value ? 'filled=1' : 'filled=0';
        else state = el.value ? 'value=' + JSON.stringify(el.value.slice(0, 24)) : 'empty';
      } else if (tag === 'textarea') {
        state = el.value ? 'value=' + JSON.stringify(el.value.slice(0, 24)) : 'empty';
      } else if (tag === 'select' && el.selectedIndex >= 0 && el.options[el.selectedIndex]) {
        state = 'selected=' + JSON.stringify(el.options[el.selectedIndex].text.slice(0, 24));
      }
      out.push({ el, prio: prio(el), y: r.top, state });
      if (el.shadowRoot) collect(el.shadowRoot);
    }
  }

  collect(document);
  // 输入类元素优先、同优先级按文档位置,截断到 MAX
  out.sort((a, b) => (a.prio - b.prio) || (a.y - b.y) || 0);
  const result = [];
  for (const item of out.slice(0, MAX)) {
    idx += 1;
    const el = item.el;
    el.setAttribute('data-agent-idx', String(idx));
    result.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      'aria-label': el.getAttribute('aria-label') || '',
      text: (el.innerText || '').trim().slice(0, 60),
      href: el.getAttribute('href') || '',
      state: item.state || '',
    });
  }
  return result;
}
""" % (SELECTOR, MAX_ELEMENTS)


class PageObservation:
    """一次观察的结果:URL + 标题 + 可交互元素索引 + 页面可见文本摘要。"""

    def __init__(self, url: str, title: str, elements: list[dict], body_text: str) -> None:
        self.url = url
        self.title = title
        self.elements = elements
        self.body_text = body_text

    def to_prompt(self) -> str:
        lines = [f"URL: {self.url}", f"标题: {self.title}", "", "可交互元素:"]
        if not self.elements:
            lines.append("  (未发现可交互元素)")
        for el in self.elements:
            idx = el["index"]
            tag = el["tag"]
            desc = el["desc"]
            lines.append(f"  [{idx}] <{tag}> {desc}")
        lines.append("")
        if self.body_text:
            lines.append("页面可见文本(节选):")
            lines.append(self.body_text)
        return "\n".join(lines)


def _safe_text(v: Any, limit: int = 60) -> str:
    if v is None:
        return ""
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:limit]


def _describe_element(el: dict) -> str:
    tag = el.get("tag", "")
    parts: list[str] = []
    if el.get("type"):
        parts.append(f"type={el['type']}")
    for key in ("placeholder", "name", "id", "aria-label"):
        v = el.get(key)
        if v:
            parts.append(f"{key}={_safe_text(v)}")
    text = el.get("text") or ""
    if text:
        parts.append(f"text={_safe_text(text, 40)}")
    href = el.get("href")
    if href:
        parts.append(f"href={_safe_text(href, 50)}")
    state = el.get("state")
    if state:
        parts.append(state)  # empty/filled/value=.../checked=N
    return " ".join(parts) if parts else tag


class Perception:
    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    def observe(self) -> PageObservation:
        page = self.session.page
        assert page is not None
        try:
            raw_elements = page.evaluate(_JS_COLLECT)
        except Exception as e:
            raw_elements = []
            print(f"[perception] 元素收集失败: {e}")
        elements = []
        for i, el in enumerate(raw_elements[:MAX_ELEMENTS], start=1):
            el["index"] = i
            el["desc"] = _describe_element(el)
            elements.append(el)
        body_text = self._visible_text()
        return PageObservation(
            url=self.session.current_url(),
            title=self.session.title(),
            elements=elements,
            body_text=body_text,
        )

    def _visible_text(self) -> str:
        page = self.session.page
        assert page is not None
        try:
            text = page.evaluate(
                "() => (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim()"
            )
            if not text:
                return ""
            return text[:MAX_BODY_TEXT]
        except Exception:
            return ""

    def to_json(self, obs: PageObservation) -> str:
        return json.dumps(
            {"url": obs.url, "title": obs.title, "elements": obs.elements, "body": obs.body_text},
            ensure_ascii=False,
        )
