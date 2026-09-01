"""小说章节爬虫拼接器 — 逐章抓取正文并合并为单体 txt。

背景:笔趣阁类小说站 99% 只提供逐章 HTML 阅读,没有全本单体 txt 下载;
正版站(起点/QQ阅读)则完全不提供打包下载。本模块解决「章节目录 → 逐章正文
→ 合并 txt」,配合检索层即可完整拿下整本书。

纯规则实现(不依赖 LLM),对齐 skills/ 层「规则引擎」定位:
- 章节列表:书页里「第N章」锚点(阿拉伯/中文数字);
- 正文提取:常见正文容器(id=content 等)优先,整页最长文本段兜底;
- 输出:合并后的 <书名>.txt,返回 章节数/完成度 供上层诚实汇报。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Optional

from pages.fetcher import fetch_page

__all__ = [
    "fetch_novel_txt",
    "extract_chapter_links",
    "extract_chapter_text",
    "clean_book_title",
]

_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 「第N章」:阿拉伯数字或中文数字(如 第125章 / 第一百二十五章 / 第1章(1/2))
_CHAPTER_RE = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十百千两]+)\s*章")

# 书页里章节锚点:第X章[标题]
_CHAPTER_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>\s*((?:第|最新章节)[^<]{0,30}章[^<]{0,40})</a>',
    re.S | re.I,
)

# 常见正文容器(按优先级)
_CONTENT_PATTERNS = (
    r'<div[^>]+id=["\']?content["\']?[^>]*>(.*?)</div>',
    r'<div[^>]+class=["\']?content["\']?[^>]*>(.*?)</div>',
    r'<div[^>]+id=["\']?chaptercontent["\']?[^>]*>(.*?)</div>',
    r'<div[^>]+class=["\']?chapter-content["\']?[^>]*>(.*?)</div>',
    r'<div[^>]+class=["\']?read-content["\']?[^>]*>(.*?)</div>',
    r'<div[^>]+id=["\']?booktext["\']?[^>]*>(.*?)</div>',
)

# 书页标题噪音(书名清洗用)
_TITLE_NOISE = re.compile(
    r"txt下载|txt全文|最新章节|最新章节列表|免费全文阅读|全文阅读|在线阅读|无弹窗|"
    r"_笔趣阁|笔趣阁|_起点中文网|_QQ阅读|_红袖读书|_云起书院|_言情小说吧|百度网盘|全文免费",
)

_MIN_CHAPTER_TEXT = 80  # 章节正文最少字数(短于此视为提取失败/空章)


def _cn_to_int(s: str) -> Optional[int]:
    """中文数字转 int:一百二十五 → 125;失败返回 None。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS.index(ch)
        elif ch in _CN_UNITS:
            u = _CN_UNITS[ch]
            total += (cur or 1) * u
            cur = 0
        else:
            return None
    return total + cur


def extract_chapter_links(html: str, base_url: str) -> list[str]:
    """从书页提取章节 URL,按章号升序、去重(同 URL 取最小章号)。

    部分站点列表含「最新章节」+「全部章节」两份,章节号是稳定排序键。
    """
    from urllib.parse import urljoin

    by_url: dict[str, int] = {}
    for href, text in _CHAPTER_LINK_RE.findall(html):
        m = _CHAPTER_RE.search(text)
        if not m:
            continue
        no = _cn_to_int(m.group(1))
        if no is None:
            continue
        url = urljoin(base_url, href)
        if url not in by_url or no < by_url[url]:
            by_url[url] = no
    ordered = sorted(by_url.items(), key=lambda kv: kv[1])
    return [url for url, _ in ordered]


def _strip_html(html: str) -> str:
    """去脚本/样式后,把标签换行并清洗空白。"""
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    import html as h

    html = h.unescape(html)
    # 压缩空行,保留段落
    lines = [ln.strip() for ln in html.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_chapter_text(html: str) -> str:
    """从章节页提取正文文本:正文容器优先,整页最长文本块兜底。"""
    if not html:
        return ""
    # 1) 常见容器
    for pat in _CONTENT_PATTERNS:
        m = re.search(pat, html, re.S | re.I)
        if m:
            text = _strip_html(m.group(1))
            # 去掉容器内导航/翻页残留(「上一章」「加入书签」等行)
            lines = [ln for ln in text.splitlines()
                     if not re.match(r"^(上一章|下一章|加入书签|推荐本书|返回目录|本章未完|请记住|www\.)", ln)]
            text = "\n".join(lines).strip()
            if len(text) >= _MIN_CHAPTER_TEXT:
                return text
    # 2) 兜底:整页去噪后,取连续正文占比最高的段落组
    text = _strip_html(html)
    paras = [p for p in text.splitlines() if len(p) >= 20]
    if not paras:
        return ""
    # 取最长的连续中文段落(正文通常是最长块)
    best, cur = "", ""
    for p in paras:
        if len(p) >= 20:
            cur = cur + p + "\n"
            if len(cur) > len(best):
                best = cur
        else:
            cur = ""
    return best.strip()


def clean_book_title(page_title: str, fallback: str = "novel") -> str:
    """从书页标题清洗出书名:去站点名/广告词,取核心词。"""
    t = _TITLE_NOISE.sub("", page_title or "").strip(" _-—|｜")
    # 若仍含分隔符,取第一段
    t = re.split(r"[\s_\-—|｜]+", t)[0].strip()
    if not t:
        t = fallback
    return t[:60]


def fetch_novel_txt(
    book_url: str,
    out_dir: str | Path = "downloads",
    title: str = "",
    max_chapters: int = 1500,
    per_page_sleep: float = 0.15,
    min_text_len: int = _MIN_CHAPTER_TEXT,
    on_progress: Optional[Callable[[int, int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    timeout: float = 15.0,
    max_time: float = 240.0,
    min_success: int = 5,
) -> dict:
    """爬取整本书并合并为 txt。

    防呆:
    - 渐进早退:前 15 章只提取到 <3 章 → 判定候选页不匹配(SEO 下载列表页常见),
      立即返回失败,不浪费数分钟;
    - 时间预算 max_time:超时停止并返回已爬部分(带 note);
    - 最小成功 min_success:有效章节不足(如 1/1500)不算成功。

    返回:
        {"ok": bool, "path": str, "chapters": int, "total": int, "title": str,
         "note": str, "error": str}
    """
    t0 = time.monotonic()
    page = fetch_page(book_url, timeout=timeout)
    if not page.html or page.error:
        return {"ok": False, "error": f"书页抓取失败: {page.error or '空页面'}"}
    links = extract_chapter_links(page.html, page.url)
    if len(links) < 2:
        return {"ok": False, "error": f"未发现章节目录(找到 {len(links)} 个章节链接)"}
    if not title:
        title = clean_book_title(page.title, fallback=Path(book_url).name)
    total = min(len(links), max_chapters)
    chunks: list[str] = [f"{title}\n\n"]
    fetched = 0
    for i, url in enumerate(links[:max_chapters], 1):
        # 渐进早退:前 15 个链接只提取到 <3 章 → 候选页是 SEO 列表/聚合页,立即放弃
        if i == 16 and fetched < 3:
            return {"ok": False,
                    "error": f"候选页不匹配(前 15 章仅提取 {fetched} 章,疑似 SEO 列表页)",
                    "chapters": fetched, "total": total, "title": title}
        if is_cancelled is not None:
            try:
                if is_cancelled():
                    break
            except Exception:
                pass
        if time.monotonic() - t0 > max_time:
            break  # 时间预算耗尽 → 返回已爬部分
        try:
            cp = fetch_page(url, timeout=timeout)
        except Exception:
            continue
        text = extract_chapter_text(cp.html)
        if len(text) < min_text_len:
            continue
        chunks.append(f"\n\n{text}\n")
        fetched += 1
        if on_progress:
            try:
                on_progress(i, total)
            except Exception:
                pass
        if per_page_sleep:
            time.sleep(per_page_sleep)
    if fetched < min_success:
        return {"ok": False, "error": f"有效章节不足({fetched}/{total}),可能站点反爬或候选页错误",
                "chapters": fetched, "total": total, "title": title}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fname = re.sub(r'[\\/:*?"<>|\r\n]', "_", title)[:80] + ".txt"
    path = out / fname
    try:
        path.write_text("".join(chunks), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写入失败: {e}"}
    note = ""
    if fetched < total:
        note = f"部分完成({fetched}/{total} 章,可能含跳章或站点反爬)"
    return {"ok": True, "path": str(path), "chapters": fetched, "total": total,
            "title": title, "note": note, "error": ""}
