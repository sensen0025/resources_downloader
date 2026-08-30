# Resource Hub — 万能下载技能(`skills/universal`)

一个入口搞定在线播放/下载难题:直链、**m3u8 切片流**、**Cloudflare 墙**、播放页自动找流。
解决「樱花动漫等站点播放页是数百个 .ts 切片组成的 .m3u8,系统无法落地」的瓶颈。

```
universal_download(url)
 ├─① 播放页/聚合页(HTML)→ 页面/JS 里提取 m3u8 流地址 → 下载第一个可用的
 ├─② Cloudflare "Just a moment" → Playwright 系统 Chrome 过 CF → 复用 cf_clearance cookie
 ├─③ m3u8 流 ── ts 切片:自带分段并发合并(零依赖,支持 AES-128)
 │            └─ fmp4/合并失败:自动 ffmpeg 兜底(ffmpeg -c copy)
 └─④ 直链 → skills/streaming(分段并发/断点续传/限速/进度)
```

## 用法(CLI)

```bash
# 万能下载:给直链 / m3u8 / 播放页都行
python -m skills.universal.cli download "https://.../play.html" --out downloads
python -m skills.universal.cli download "https://.../index.m3u8" --json

# 只看播放页里有哪些流(排查用)
python -m skills.universal.cli resolve "https://.../play.html"

# ffmpeg 是否可用(fmp4 流合并需要它)
python -m skills.universal.cli ffmpeg
```

## 三个关键设计

1. **CF 反制不是魔法,是"浏览器拿门票"**:requests 解不了 JS 挑战,但系统 Chrome
   能过。`browser_cookies()` 打开 URL → 轮询标题等 "Just a moment" 消失 →
   取 `cf_clearance` 等 cookie 给 requests 复用(几分钟有效),同时持久化到
   `accounts/cookies/`。诚实边界:重度 CF(交互式校验)可能超时,此时 Agent
   用 `use_session=true` 整体走浏览器;
2. **m3u8 双通道**:ts 明文切片走 `skills/streaming` 自带合并(纯 Python,快);
   fmp4(`#EXT-X-MAP`)或自带合并失败 → 自动切 ffmpeg(`-c copy` 重封装)。
   ffmpeg 未安装时给出明确安装提示,不静默产出损坏文件;
3. **播放页自动找流**:正则扫 HTML/JS 里的 `.m3u8`(引号/转义/video source 多种形态),
   相对地址绝对化,按序尝试,第一个能落地的就是结果 —— 解决"AI 探测到播放接口
   但无法交付"。

## 集成

- AI 工具:`universal_download` 已注册(AgentCore 目录);`agent/tools.py` 的
  `download` 工具对 `.m3u8`/播放流自动路由到这里;
- 提示词:Agent 遇到 m3u8/播放页/CF 时按「万能下载」策略行动,不放弃候选;
- 广告过滤(`skills/adblock`)同步生效:播放页/聚合页里的广告链接不进候选。

## 测试

```bash
python -m unittest tests.test_universal tests.test_adblock -v
```

覆盖:播放页 m3u8 提取、m3u8 落地(复用本地服务器)、CF 挑战文本识别、
`resolve_stream` 多形态正则、ffmpeg 检测降级、工具注册;广告:域名/URL 模式/
文本标记命中、search 候选剔除、页面链接不进候选。
