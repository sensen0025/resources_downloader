# resources_downloader / Resource Hub

让 **DSH 智能体自己会下载任何资源** 的通用技能包与全能下载引擎。

```
用户需求(任意站点 / 任意资源 / 专用下载器 / 验证码 / 网盘 / 爬虫)
        │
        ▼
DSH / AI Harness 核心大脑(规划、多轮、失败重试、自动探针交付)
        │
   ┌────┴──────────────────────────┐
   ▼                               ▼
.dsh/skills/ (17+ 知识技能卡)   plugin/ (rd-tools 插件) + python skills
```

---

## 核心能力与知识技能卡 (`.dsh/skills/`)

| 技能卡 | 类型 | 说明与实测状态 |
|---|---|---|
| `site-directory` | 目录导航 | 专用下载器 A 表 + 通用知识卡 B 表总索引 |
| `site-bilibili-bbdown` | 专用下载器 | B站/番剧/合集/弹幕首选 BBDown（✅ v1.6.3 + ffmpeg 实测） |
| `site-videos-yt-dlp` | 专用下载器 | YouTube / 千站视频首选 yt-dlp（✅ 2026.08.19 + node/ffmpeg 实测） |
| `site-annas-archive` | 站点知识卡 | Anna's Archive (https://annas-archive.gd/) DDoS-Guard 过盾 + 慢速合作节点直链下载（✅ 实测） |
| `site-project-gutenberg` | 站点知识卡 | 古登堡公版书直链提取（✅ 实测） |
| `site-open-access-papers`| 站点知识卡 | 学术论文 OA 链路 (Crossref→Unpaywall→Europe PMC)（✅ 实测） |
| `site-huggingface-datasets`| 站点知识卡 | HF 数据集流式取样与下载（✅ 实测） |
| `site-littleskin` | 站点知识卡 | LittleSkin 皮肤/材质下载（✅ 实测） |
| `site-quark-netdisk` | 站点知识卡 | 夸克网盘分享解析与转存下载 |
| `resource-download` | 通用方法论 | 资源下载顶层规划、澄清意图与交付 |
| `download-and-verify` | 通用方法论 | 探针校验（魔数/大小/哈希）与产物交付纪律 |
| `write-and-run-crawler` | 通用方法论 | 针对冷门站点的临时 Python 爬虫编写与运行 |
| `captcha-handling` | 通用方法论 | 验证码识别与绕过 |
| `site-memory` | 通用方法论 | 站点成功经验与坑点沉淀 |

---

## 工具底座 (`plugin/` 与 Python Skills)

- **`plugin/` (rd-tools)**：Cordis 工具插件，提供 `run_code`、`http_fetch`、`web_search`、`download_file`（断点续传）、`download_hls`、`probe_file`、`browser`（Playwright 浏览器自动化与验证码处理）、`memory_remember`/`memory_query`。
- **Python 引擎 (`skills/`, `pages/`, `search/`, `api/`, `web/`)**：
  - 邮箱验证码自动收码 (`skills/mail`)
  - 视觉/滑块验证码解析 (`skills/captcha`)
  - 大文件流式与 HLS 下载 (`skills/streaming`)
  - 网页控制台与 RESTful API (`web/`, `api/`)

---

## 本地快速开始

```bash
# 1. 运行单测
node plugin/tests/run.mjs
for f in plugin/index.js plugin/lib/*.js; do node --check "$f"; done

# 2. 启动服务控制台 (可选)
uvicorn api.app:app --port 8000
```

## License

MIT © 2026 sensen0025
