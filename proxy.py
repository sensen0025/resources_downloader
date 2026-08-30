"""VPN/代理配置 — 通用模板(可复用):一处配置,资源获取全链路生效。

优先级: RH_PROXY_URL > HTTPS_PROXY/https_proxy > HTTP_PROXY/http_proxy
与晨涧云学术代理(proxy.mornai.cn:7890)等兼容,也兼容标准
`export http_proxy=...` 写法(requests 原生识别标准变量,本模块补 RH_PROXY_URL)。

生效范围(按「资源获取链路」划分,不建议全局代理 —— 见 README「代理配置」):
  ✅ search/(多引擎检索)  pages/(页面抓取)  delivery/(文件下载)  agent/browser(Playwright)
  ❌ LLM API(agent/llm.py、ai/vision.py、tools_vision.py):保持直连
     (代理是给资源下载加速的,LLM 走代理反而引入额外延迟/失败风险)
  ❌ api/webhooks.py(服务端→客户回调):保持直连

用法:
    import requests
    from proxy import proxies, session, apply_proxies, playwright_proxy

    requests.get(url, proxies=proxies())          # 一次性请求
    s = session(); s.get(url)                     # 会话级(自动带代理)
    apply_proxies(existing_session)               # 给已有 Session 补代理
    browser.new_context(proxy=playwright_proxy()) # Playwright(未配置返回 None)
"""

from __future__ import annotations

import os
from typing import Optional

from skills.mail.config import load_dotenv

__all__ = [
    "proxy_url",
    "is_enabled",
    "proxies",
    "session",
    "apply_proxies",
    "playwright_proxy",
    "export_env_template",
]

_NO_PROXY = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "localhost,127.0.0.1,::1"


def _resolve() -> str:
    """解析代理 URL:RH_PROXY_URL > HTTPS_PROXY > HTTP_PROXY(含小写变体)。"""
    load_dotenv()
    for key in ("RH_PROXY_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(key, "")
        if v and v.strip():
            return v.strip()
    return ""


def proxy_url() -> str:
    """当前生效的代理 URL(未配置返回空串)。"""
    return _resolve()


def is_enabled() -> bool:
    return bool(_resolve())


def proxies() -> dict:
    """requests 用的 proxies 字典(未配置返回空 dict,不拦截标准环境变量)。"""
    p = _resolve()
    if not p:
        return {}
    return {"http": p, "https": p, "no_proxy": _NO_PROXY}


def session():
    """新建带代理的 requests.Session(未配置代理时与普通 Session 等价)。"""
    import requests

    s = requests.Session()
    s.proxies.update(proxies())
    return s


def apply_proxies(sess) -> None:
    """给已有 requests.Session 补代理(幂等,未配置代理则无操作)。"""
    sess.proxies.update(proxies())


def playwright_proxy() -> Optional[dict]:
    """Playwright browser.new_context(proxy=...) 参数;未配置返回 None。"""
    p = _resolve()
    if not p:
        return None
    return {"server": p, "bypass": _NO_PROXY}


def export_env_template() -> str:
    """通用模板(可直接复制到 shell / CI / 交接文档)。"""
    return _TEMPLATE


_TEMPLATE = """# ===== Resource Hub VPN/代理 通用模板(适配晨涧云 proxy.mornai.cn:7890) =====
# 只对「资源获取链路」生效:多引擎检索、页面抓取、文件下载、浏览器 Agent。
# LLM API 与 Webhook 回调保持直连(不建议全局代理,网速会变慢)。

# 1) 项目级(推荐):resource-hub/.env 加一行,或当前 shell 导出
export RH_PROXY_URL=http://proxy.mornai.cn:7890
# 等价的标准变量写法(requests/curl/wget/git 原生识别):
# export http_proxy=http://proxy.mornai.cn:7890
# export https_proxy=http://proxy.mornai.cn:7890
# export no_proxy=localhost,127.0.0.1

# 2) pip 单次代理
# pip install xxx --proxy=http://proxy.mornai.cn:7890

# 3) git:只代理 GitHub,国内仓库不受影响
# git config --global http.https://github.com.proxy http://proxy.mornai.cn:7890
# git config --global https.https://github.com.proxy http://proxy.mornai.cn:7890
# 取消: git config --global --unset http.proxy

# 4) curl / wget 单次
# curl -x http://proxy.mornai.cn:7890 https://example.com
# wget -e use_proxy=yes -e http_proxy=http://proxy.mornai.cn:7890 \\
#      -e https_proxy=http://proxy.mornai.cn:7890 https://example.com

# 5) conda
# conda config --set proxy_servers.http http://proxy.mornai.cn:7890
# conda config --set proxy_servers.https http://proxy.mornai.cn:7890

# 6) Docker(pull/build/container 运行时走代理)
#   /etc/systemd/system/docker.service.d/proxy.conf:
#   [Service]
#   Environment="HTTP_PROXY=http://proxy.mornai.cn:7890"
#   Environment="HTTPS_PROXY=http://proxy.mornai.cn:7890"
#   Environment="NO_PROXY=localhost,127.0.0.1"
#   sudo systemctl daemon-reload && sudo systemctl restart docker

# 7) 浏览器:Chrome 用 SwitchyOmega / Firefox 在 Network Settings 里
#    代理服务器 proxy.mornai.cn,端口 7890(用完成记得关掉)

# 8) 关闭/切换:unset RH_PROXY_URL http_proxy https_proxy 即可恢复直连
"""
