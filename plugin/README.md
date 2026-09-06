# rd-tools —— resources_downloader 通用工具插件

DSH (cordis) 工具插件，向 agent 注册 6 个**通用、与站点无关**的执行工具。
纯 Node 实现，零外部运行时依赖（Node ≥ 18.17）。

## 工具清单

| 工具 | 作用 | 何时用 |
|---|---|---|
| `run_code` | 执行 LLM 现场写的 python/bash/node 脚本（硬超时、输出截尾） | 写一次性爬虫/解析脚本并运行 |
| `http_fetch` | GET/HEAD 任意 URL（跟随跳转），返回文本或存文件 | 侦察页面/JSON 接口/m3u8 清单 |
| `web_search` | 多引擎搜索（Bing/Mojeek/DDG Lite），失败逐引擎上报 | 没链接时找源；可退回 DSH 自带 web_search |
| `download_file` | 直链下载：Range `.part` 断点续传 + sha256 + 跳转 | 有真实文件 URL |
| `download_hls` | m3u8 下载：主清单选最高清晰度、AES-128 解密、按序合并 | 流媒体/播放页解析出的 m3u8 |
| `probe_file` | 探针校验：大小/魔数类型/扩展名/头字节/可选 sha256 | 交付前必验，识破“空文件/HTML 错误页冒充媒体” |
| `browser` | 通用浏览器自动化（可选，Python playwright）：open/act/eval/cookies/download/screenshot/solve/close；**每 profile 一个持久会话**（登录一次页面与 Cookie 都在，可跨调用完成多步流程） | 登录墙、JS 挑战、反爬（403/CF/zhihu）、点击下载 |
| `memory_remember` | 持久记忆某站点/数据集/镜像并打分（1–5）与标签/方法；同域重复=合并新证据 | 每个资源任务收尾（见 site-memory 技能） |
| `memory_query` | 查记忆：精确域/别名/标签/类型/verdict，`similar=true` 找相似站排序 | 开始任务前/找同类镜像时 |

`browser` 的 `solve`（验证码，见 `captcha-handling` 技能）：`kind=ocr/slider/geetest`。
识别依赖可选：`pip install ddddocr`（字符 OCR+滑块缺口，MIT）、
`pip install git+https://github.com/xKiian/GeekedTest.git`（极验 v4 slide/icon/gobang/ai，MIT）。
未装时返回明确降级提示。

`browser` 说明：会话用持久 profile（登录一次 Cookie 即存），可 `cookies` 导成
`Cookie:` 头再配 `download_file` 用。宿主机需
`pip install playwright && playwright install chromium`（缺省时工具明确报错，不影响其他工具）。

## 注册进 DSH profile

```bash
dsh plugin --profile web add "link:/abs/path/to/resources_downloader/plugin"
```

随后按部署的 loader 语义启用并配置（可选配置项见下），重启 profile 生效：
skills（`.dsh/skills/`）负责告诉 agent 何时调这些工具、失败怎么重试。

## 配置（均为可选）

| 键 | 默认 | 说明 |
|---|---|---|
| `root` | 启动 cwd | 工作根目录 |
| `downloadsDir` | `<root>/downloads` | 下载落盘目录 |
| `scratchDir` | `<root>/.rd_scratch` | 脚本执行目录 |
| `maxConcurrency` | 4 | HLS 分段并发上限 |
| `timeoutMs` | 120000 | 默认请求超时 |
| `python` | `python3` | run_code 的 python 解释器 |
| `memoryDir` | `<root>/memory` | 记忆存储目录（JSONL，运行态、已在仓库 .gitignore） |

## 测试（离线，无需联网/无需 dsh）

```bash
node plugin/tests/run.mjs
```

测试用本地 HTTP 夹具覆盖：直链下载、Range 断点续传、m3u8 主清单选流与按序合并、
HTTP 跳转、run_code、probe_file。

## 安全说明

- `run_code` 会执行 agent 生成的代码：**只应在本仓库技能设定的“现场写爬虫”场景由受信 agent 调用**；部署时可按 profile 的审批策略（approval/sandbox）对它做门控。
- 下载产物默认落在 `downloads/`（已在仓库 `.gitignore`），不会被提交。
