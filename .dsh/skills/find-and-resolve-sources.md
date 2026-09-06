---
name: find-and-resolve-sources
description: 找源与解析。没有现成下载链接时，用搜索与页面侦察找到候选资源页/直链/接口/网盘分享；把「网页」「分享页」「播放页」解析成真实可下载的 URL（直链或 m3u8）。为 resource-download 提供“货源”。
whenToUse: 用户给的是关键词而非直链、或给的是网页/网盘分享/播放页/聚合页、或直链下载 403/失效需要换源时。
---

# find-and-resolve-sources（找源与解析）

把「关键词或一个页面」变成「能下得动的真实 URL 清单」。来源千变万化，但思路通用。

## 1. 找候选源（没有链接时）
- 多引擎 `web_search`（一次多给几个查询词：中文名/英文名/作者+类型/站点名）；对结果用 `http_fetch`/`web_fetch` 快速探活，剔除 404/死链/纯导航页。
- 优先候选：官方/作者发布页 > 有真实文件附件的页面 > 接口可见的页面。
- 记录候选时带**来源页面标题与 URL**，便于后续核对“这个文件确实是用户要的那个”。

## 2. 解析页面拿到真实 URL（核心手艺）
页面 ≠ 文件。拿到页面后按顺序找：
1. **显式下载**：`<a download>`、`href` 指向文件扩展名、`?download=`、`/raw/`、`/file/`。
2. **后端/数据接口**：看页面里的 `fetch`/`xhr` 调用、`<script>` 里的 JSON、`window.__DATA__`、JSON-LD；直接请求接口拿 JSON，比剥 HTML 稳。
3. **内嵌流媒体**：页面提到 `.m3u8`、`video` 标签、`hls`、`playlist`、`dash` 时 → 抓出流清单地址，交给 `download_hls`。
4. **网盘/中转/需登录**：识别网盘分享页（分享/转存/提取码/「登录后下载」字样）→ 不要硬刚页面；
   纯 HTTP 被反爬/JS 挡住时改用 `browser` 打开页面取直链或点击下载；仍需登录就请用户提供 Cookie/直链。
- 判断文件与请求是否匹配：文件扩展名/Content-Type/页面标题/元数据都要对上，不确定就问用户。

## 3. 直链的健康检查
解析出候选 URL 后先 HEAD/GET 一小段验证：状态 200、Content-Length>0、Content-Type 合理。403/CF/跳登录页的 URL 标记为「需凭证」，转交 write-and-run-crawler 带 Cookie/Header 处理。

## 4. 交付物
给下一步（resource-download）输出一份结构化货源：
```jsonc
{
  "sources": [
    { "kind": "direct|page|api|stream|pan",
      "url": "…", "title": "…", "type": "video/audio/…",
      "needs": "none|cookie|login", "sizeBytes": 1234567 }
  ]
}
```
找不到任何可下载源时，如实说清楚试过哪些关键词/站点、失败原因，不要编造 URL。
