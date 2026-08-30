# Resource Hub — 邮箱验证码收码技能(`skills/mail`)

Resource Hub 主链路里的「收验证码」环节(计划文档 §4.3 的 `read_mail` 技能)。
**用你自己的真实邮箱(IMAP)收注册/登录验证码**,零第三方依赖(纯 Python 标准库),Python 3.9+ 即可运行。

```
目标站 → 发验证码邮件 → 你的邮箱(IMAP) → 本技能轮询 → 正则提取 → Agent 拿到 code
```

---

## 快速开始

### 1. 配置邮箱(以 QQ 邮箱为例)

1. 打开 QQ 邮箱 → 设置 → 账户 → 开启 **IMAP/SMTP 服务**,按提示获取**授权码**(16 位,不是登录密码);
2. 在项目根目录建 `.env`(可复制 `.env.example`):

```ini
MAIL_IMAP_HOST=imap.qq.com
MAIL_IMAP_PORT=993
MAIL_EMAIL=你的QQ邮箱@qq.com
MAIL_PASSWORD=你的16位授权码
MAIL_FOLDER=INBOX
```

### 2. 常用邮箱 IMAP 参数

| 邮箱 | IMAP 主机 | 端口 | 凭证 |
|---|---|---|---|
| QQ 邮箱 | `imap.qq.com` | 993 | 设置里开的「授权码」 |
| 163 邮箱 | `imap.163.com` | 993 | 设置里开的「授权码」 |
| Gmail | `imap.gmail.com` | 993 | 两步验证开启后生成「应用专用密码」 |
| Outlook/Office365 | `outlook.office365.com` | 993 | 应用密码(或普通密码) |

> 密码一律填**授权码/应用专用密码**,不要把真实登录密码写进 `.env`。

### 3. 使用

```bash
# 一次性检查未读邮件,提取所有验证码
python -m skills.mail.cli check

# 只关注某个发件人(如刚注册的站点)
python -m skills.mail.cli check --sender example.com

# 持续轮询,新邮件实时打印(演示/联调最常用)
python -m skills.mail.cli watch --interval 10

# 等待一封含验证码的邮件,带预算(超时返回退出码 3)——Agent 技能就是调这个
python -m skills.mail.cli wait --sender github.com --timeout 120

# 所有命令都支持 --json,给脚本/Agent 消费
python -m skills.mail.cli check --json
```

退出码:`0`=找到验证码,`2`=检查完毕但没找到,`3`=等待超时。

---

## 作为 Agent 技能怎么用(核心 API)

```python
from skills.mail import ImapMailbox

mb = ImapMailbox(
    host="imap.qq.com",
    email="me@qq.com",
    password="授权码",
)
result = mb.wait_for_code(
    sender_hint="example.com",   # 只收这个发件人的验证码(域名/邮箱/主题子串)
    timeout=120,                 # 等待预算,防死等
    poll_interval=5,
    mark_seen=True,              # 提取成功后标记已读(消费语义)
)
if result:
    print(result.code)      # "123456"
    print(result.source)    # "body" / "subject"
    print(result.pattern)   # 命中的提取规则,便于排障
    print(result.context)   # 命中位置上下文,供人工核对
else:
    print("超时未收到验证码")
```

一次注册流程里,`wait_for_code` 就是 Agent 在「点完提交按钮后」阻塞等待的那一步:
填表提交 → 轮询等邮件 → 取到 code → 回填输入框。整套 `ImapMailbox` 已经是带
预算、带断线重连、带发件人过滤和去重的就绪组件,可直接注册为 Agent 工具。

---

## 验证码提取规则

两层策略(规则在 `skills/mail/extractor.py` 的 `PATTERNS`):

1. **精确模式**:关键字(`验证码/校验码/确认码` / `verification code/OTP/code/pin`)+ 分隔符 + 4~10 位数字;
2. **行内兜底**:整行含关键字时,取行内第一个独立的 4~8 位数字(处理「请勿向任何人透露验证码,请牢记:123456」这类措辞)。

支持:纯文本 / HTML(自动去标签)/ 验证码在主题里(`--subject-first` 可让主题优先)。
没有关键字时**不猜**(避免把日期、订单号、电话号码误当验证码)。

**加新格式**:在 `PATTERNS` 里加一条正则即可,不用改别的。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖:中文纯文本、英文 HTML、验证码在主题、关键字与数字分离(兜底)、无验证码邮件、主题优先标志。

---

## 设计说明与后续扩展

- **为什么用 IMAP 轮询**:真实邮箱对目标站接受度最高(临时邮箱域名常被拉黑),个人版零额外成本;
- **为什么纯标准库**:`imaplib`/`email`/`html.parser`/`re` 够用,个人版保持「零依赖一键跑」;
- **与计划文档的对应**:这是 §4.3 技能表里 `read_mail` 的「自建 catch-all SMTP 轮询」的替代实现(用个人真实邮箱);
- **后续可插拔后端**(接口已按此设计):临时邮箱 API(mail.tm / 1secmail)、自建 catch-all SMTP(aiosmtpd)、IMAP IDLE 推送替代轮询;
- **Agent 集成**:下一步在 agent 循环里注册为工具,接「填表提交 → wait_for_code → 回填验证码」的完整动作链。

---

## 🤖 账号注册器/登录器(AI Agent 驱动,`run_account.py`)

一条命令自动注册/登录任意站点:LLM(DeepSeek)看页面 → 决定点击/填表 → 邮箱验证码/安全链接自动收 → 图形验证码走视觉模型。**已实测两个站点全流程跑通**:

| 站点 | 验证方式 | 结果 |
|---|---|---|
| claude.ai | 邮箱安全链接(非验证码)+ hCaptcha/Cloudflare | 注册 ✅ 登录 ✅ |
| tripo3d.ai | 邮箱验证码登录(填邮箱→Send Code→收码→登录) | 注册 ✅ |

```powershell
# 用系统 Python(已装 playwright + 有 Chrome)
python run_account.py register claude --email your+alias@gmail.com
python run_account.py register tripo --email your+tripo@gmail.com
python run_account.py login claude --email xxx@gmail.com --headed   # 有头模式,能看到操作

# 登录态 Cookie 加速:首次登录/注册成功后自动存 accounts/cookies/(按 站点+邮箱 分文件),
# 再次 login 命中登录态直接跳过 Agent 流程(快速通道,零 LLM 成本)
python run_account.py login claude --email xxx@gmail.com
python run_account.py login claude --email xxx@gmail.com --forget-cookies   # 删除旧 Cookie,强制重登
```

- 默认无头,用系统 Chrome(channel=chrome)过 Cloudflare 更稳;
- 密码自动生成保存到 `accounts/credentials.json`(已 gitignore);
- 登录态 Cookie 存 `accounts/cookies/`(已 gitignore):`BrowserSession(cookies_path=...)` 自动注入,
  `run_account.py` 登录走 Cookie 快速通道;`fetch_resource` 的浏览器 Agent 也按站点域复用同域登录态;
- 新站点 = 在 `accounts/` 加一个场景定义(如 `accounts/tripo.py`),Agent 自动适配 —— 已验证「验证码登录」和「安全链接」两种邮箱验证格式;
- 需在 `.env` 配 `LLM_API_KEY`(DeepSeek),邮箱验证复用 `skills/mail`。

**已验证能力**:claude.ai 注册/登录 ✅、tripo3d.ai 验证码注册 ✅、邮箱安全链接与验证码自动提取 ✅、hCaptcha/Cloudflare 校验自动等待 ✅、LLM 推理占满预算自动重试 ✅、人工介入钩子 ✅

---

## 其他技能与模块

| 模块 | 位置 | 说明 |
|---|---|---|
| 🔎 资源检索 | `search/` | 多搜索引擎聚合(bing/baidu/mojeek/360/ddg/github)+ 去重打分 + HTTP 可用性探测,文档见 `search/README.md` |
| 📄 页面分析 | `pages/` | 找源核心:抓取 → 元数据/下载链接提取 → 分级(直链/下载页/网盘/需登录/聚合/反爬),文档见 `pages/README.md` |
| 🤖 AI 核心 | `ai/` | **AI Harness(DSH 同构)**:AgentCore 单一循环 + 全技能注册表;AI 自主规划(意图绑定)→ 检索(语义重排)→ 分析 → 下载(换源/换格式)→ 验证;决策全审计 |
| 🛠️ 技能内核 | `skills/core.py` | DSH 式 `@tool` 注册表:单点定义工具契约、执行前参数强校验、catalog 直喂 LLM |
| 📮 邮箱收码 | `skills/mail/` | IMAP 轮询真实邮箱收验证码,纯标准库,已实测 Gmail |
| 🖼️ 图形验证码 | `skills/captcha/` | ddddocr 本地 OCR + 检测裁剪 + 多变体投票 + 三态置信度 + 可选 VLM 兜底,文档见 `skills/captcha/README.md`(注意它需要 `venv-demo` 的 Python,内含 ddddocr/opencv) |
| 🛡️ 下载查毒 | `skills/security/` | **下载文件安全性校对**:开源杀毒 ClamAV(clamd/clamscan)+ YARA 规则 + 内置启发式(魔法字节伪装/压缩炸弹/脚本载荷/双重扩展名);`scan_file` 工具 + 下载链路安全闸门,文档见 `skills/security/README.md` |
| ⚡ 流式下载 | `skills/streaming/` | **音视频大文件专用**:直链分段并发(分片级断点续传)+ HLS/m3u8(分段并发/AES-128 加密)+ 边下边交付 + 限速 + 实时进度;`download_stream` 工具,media 类型自动分流,文档见 `skills/streaming/README.md` |
| 🌀 万能下载 | `skills/universal/` | **一个入口搞定在线播放**:播放页/聚合页自动找 m3u8 流并合并落地;Cloudflare "Just a moment" 浏览器会话反制(cf_clearance 复用);fmp4 自动 ffmpeg 兜底;`universal_download` 工具,`download` 对 m3u8/CF 自动路由,文档见 `skills/universal/README.md` |
| 🚫 广告过滤 | `skills/adblock/` | **类 EasyList 广告数据池**:广告域名/URL 模式/文本标记,检索候选剔除 + 页面链接不进候选 + 相关性扣分 |
| 🤖 资源任务 | `agent/tasks/` | `fetch_resource()`:检索→分析→直链快路径→浏览器 Agent 慢路径(登录/CF/下载) |
| ⬇️ 下载交付 | `delivery/` | `.part` 断点续传 + Range 校验 + hash/大小校验 |
| 🌐 HTTP API | `api/` | 客户资源下载接口:令牌申请(每人独立,可查历史)+ 任务契约(状态机/事件流)+ 传输(轮询/SSE 实时/Webhook 签名回调)+ 文件交付(**Range 断点续传**/zip),文档见下方「客户 API」 |
| 🖥️ 网页控制台 | `web/` | **可视化操作界面(网页,零前端构建链)**:下载任务(SSE 实时进度)+ 历史 + **配置(LLM key/邮箱/代理,写 .env 立即生效)** + 令牌管理 + 查毒,见下方「网页控制台」 |

```powershell
# AI Harness 核心:一条请求,AI 自主完成(意图→检索→分析→下载→验证)
python -c "from ai import AgentCore, HarnessConfig; r = AgentCore(HarnessConfig()).run('凡人修仙传壁纸'); print(r.success, r.files)"

# 资源检索(核心模块):多引擎聚合 + 可用性筛选
python -m search.cli "python requests 中文文档 pdf" --probe --limit 10

# 页面分析:抓取→提取→分级
python -m pages.cli "https://www.minecraft-schematics.com/schematic/31398/" --probe

# 端到端资源任务:检索→分析→(登录)→下载 .litematic/.schematic
python -c "from agent.tasks.fetch_resource import fetch_resource; r = fetch_resource('Goldencrest Manor minecraft schematic', login_email='you+alias@gmail.com'); print(r)"

# 运行图形验证码技能需用装了 ddddocr 的 Python:
& .\resource-hub-research\venv-demo\Scripts\python.exe -m skills.captcha.cli solve captcha.png

# 下载安全查毒:查看引擎可用性 → 扫描文件/目录
python -m skills.security.cli engines
python -m skills.security.cli scan downloads/xxx.litematic

# 流式下载(音视频大文件):探测 → 下载(直链分段并发 / HLS 自动识别)
python -m skills.streaming.cli probe https://example.com/movie.mp4
python -m skills.streaming.cli download https://example.com/movie.mp4 --segments 8 --limit 2M

# 万能下载(动漫/影视播放页、m3u8 切片流、Cloudflare 墙)
python -m skills.universal.cli download "https://.../play.html" --out downloads
python -m skills.universal.cli resolve "https://.../play.html"    # 先看页面里有哪些流
python -m skills.universal.cli ffmpeg                             # fmp4 合并需要 ffmpeg

# 客户 API:启动服务
uvicorn api.app:app --port 8000

# 打开浏览器访问网页控制台(下载任务/配置/历史/令牌/查毒 都在这里)
#   http://127.0.0.1:8000/
#   首次使用:配置页填 LLM API Key + 邮箱授权码 → 保存(立即生效)→ 下载页提交任务
#   令牌模式(RH_API_SECRET)下:令牌页申请 token → 右上角「令牌登录」

# ① 申请令牌(每人独立;服务端配 RH_API_SECRET 后必须 Bearer 认证)
curl -X POST http://127.0.0.1:8000/api/v1/tokens -H "Content-Type: application/json" -d '{"name":"my-app"}'
#   → {"code":0,"data":{"token_id":"...","token_key":"rh_live_xxx","owner":"...","note":"明文仅此一次"}}

# ② 提交资源下载任务(令牌模式加 -H "Authorization: Bearer <key>")
curl -X POST http://127.0.0.1:8000/api/v1/tasks -H "Content-Type: application/json" \
  -d '{"query":"故宫 投影 litematic","label":"我的任务","callback_url":"https://my.com/webhook"}'
#   → {"code":0,"data":{"task_id":"...","status":"queued","polling_url":"...","events_url":"...","file_token":"..."}}

# ③ 等结果:轮询 / SSE 实时 / Webhook 三选一
curl http://127.0.0.1:8000/api/v1/tasks/{task_id}
curl -N http://127.0.0.1:8000/api/v1/tasks/{task_id}/events      # SSE 实时进度

# ④ 断点续传下载(206 + Content-Range,中断后带 Range 续传)
curl -L -C - -o res.litematic http://127.0.0.1:8000/api/v1/tasks/{task_id}/files/res.litematic
curl http://127.0.0.1:8000/api/v1/tasks/{task_id}/download-all -o all.zip   # 打包下载

# ⑤ 历史 / 取消 / 令牌管理
curl "http://127.0.0.1:8000/api/v1/tasks?limit=20&status=done"    # 我的历史(仅本人)
curl -X POST http://127.0.0.1:8000/api/v1/tasks/{task_id}/cancel  # 协作式取消
curl http://127.0.0.1:8000/api/v1/tokens                          # 我的令牌
curl -X POST http://127.0.0.1:8000/api/v1/tokens/{token_id}/rotate   # 轮换(旧钥作废)
curl -X DELETE http://127.0.0.1:8000/api/v1/tokens/{token_id}     # 吊销
```

## VPN/代理配置(通用模板)

资源获取链路(多引擎检索 / 页面抓取 / 文件下载 / 浏览器 Agent)统一支持代理,
**一处配置全链路生效**;LLM API 与 Webhook 回调保持直连(不建议全局代理)。

```bash
# 方式一:项目级 .env(推荐,见 .env.example)
RH_PROXY_URL=http://proxy.mornai.cn:7890

# 方式二:shell 一键 source 通用模板(也可手动 export)
source proxy_template.sh

# 方式三:标准环境变量(requests/curl/wget/git 原生识别)
export http_proxy=http://proxy.mornai.cn:7890
export https_proxy=http://proxy.mornai.cn:7890
export no_proxy=localhost,127.0.0.1

# 其他工具:pip --proxy=... / git config http.https://github.com.proxy ... /
# conda config --set proxy_servers.http ... / Docker HTTP_PROXY 环境变量
# 完整模板见 proxy_template.sh 或 python -c "from proxy import export_env_template; print(export_env_template())"
```

实现:根目录 `proxy.py`(优先级 `RH_PROXY_URL` > `HTTPS_PROXY` > `HTTP_PROXY`),
已接入 `search/engines`、`search/probe`、`pages/fetcher`、`delivery/downloader`、`agent/browser`。

# 端到端带安全闸门的资源任务(下载后自动查毒,检出恶意即删除换源)
python -c "from agent.tasks.fetch_resource import fetch_resource; r = fetch_resource('故宫 投影 litematic'); print(r)"
```
