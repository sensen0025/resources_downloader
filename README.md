# resources_downloader

让 **DSH 智能体自己会下载任何资源** 的通用技能包 —— 不维护任何站点专有代码。

```
用户需求(任意站点 / 任意资源 / 专用下载器 / 验证码 / 爬虫 / 多轮重试)
        │
        ▼
DSH = 唯一的大脑(规划、多轮策略、失败重试、探针校验交付)       ← 核心
        │
  本仓库只做两件事 ────────────────────────────────
  ① .dsh/skills/   教 agent 怎么干的“方法论”与资源站技能卡 (17 张)
  ② plugin/        cordis 通用、稳定、与站点无关的执行工具底座
```

## 设计原则

用户的请求不可枚举：新站、冷门资源、登录墙、反爬、要“自己写个爬虫抓”……
给每个站点写死一个适配器 = 永远在维护会过期的代码。

本仓库反过来：**把“会干活”的能力全给 LLM** —— 写程序、运行执行、搜索、抓页面、确定性下载、验证。
- **通用能力工具**：零站点专有常驻代码；
- **专用成熟下载器**：优先使用社区维护的成熟 CLI（如 B站 BBDown、YouTube yt-dlp）；
- **失败多轮换法**：技能文档规定了“多轮策略换法重试”的纪律（证据驱动，不盲试）；
- **交付必须验证**：探针校验大小/魔数/哈希后才算成功。

---

## 仓库内容

| 路径 | 内容 |
|---|---|
| `.dsh/skills/*.md` | **17 个 DSH 技能卡**：通用 6 个（`resource-download`、`find-and-resolve-sources`、`write-and-run-crawler`、`download-and-verify`、`site-memory`、`captcha-handling`）+ `site-directory` 总目录 + **资源站知识卡与专用下载器卡**（`site-bilibili-bbdown`、`site-videos-yt-dlp`、`site-annas-archive`、`site-project-gutenberg`、`site-open-access-papers`、`site-huggingface-datasets`、`site-littleskin` 等） |
| `plugin/` | cordis 工具插件 `rd-tools`：`run_code`、`http_fetch`、`web_search`、`download_file`（断点续传）、`download_hls`（m3u8/AES-128）、`probe_file`（魔数/哈希验证）、`browser`（可选：Python playwright 通用浏览器自动化：持久会话/登录/反爬/点击下载/导 Cookie/验证码识别）、`memory_remember`/`memory_query`（站点记忆与打分） |
| `plugin/lib/` | 各工具独立实现 |
| `plugin/tests/run.mjs` | 离线单测套件（本地 HTTP 夹具） |
| `docs/` | 技能卡与专用下载器接入规范文档 |
| `.github/workflows/ci.yml`| 自动化 CI：语法检查 + 离线单测 |

---

## 安装与使用

### 1. 技能包
把仓库作为 DSH 的 workspace（在该目录开会话）即可自动发现 `.dsh/skills/`；
也可将 `.dsh/skills/` 软链或复制进任意项目的同名目录。

### 2. 工具插件（注册进 profile 如 `web`/`headless`）
```bash
dsh plugin --profile web add "link:/path/to/resources_downloader/plugin"
```

### 3. 环境依赖
- **Node** ≥ 18.17（JS 插件零外部依赖）
- **ffmpeg**：音视频合流必需
- **专用下载器**（宿主机）：
  - **BBDown** (`~/bin/BBDown`)：B站下载首选
  - **yt-dlp** (`~/bin/yt-dlp`)：YouTube 及千站通用音视频首选
- **可选依赖**：`playwright`（浏览器自动化）、`ddddocr`（验证码识别）

---

## 开发与测试

```bash
# 离线单测
node plugin/tests/run.mjs

# 语法检查
for f in plugin/index.js plugin/lib/*.js; do node --check "$f"; done
```

## License

MIT © 2026 sensen0025
