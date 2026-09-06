# Cookie Vault（本机登录态接入）— 架构说明

> 隐私红线：本功能把**用户个人浏览器的登录 Cookie** 提供给下载引擎与无头 agent 使用。
> Cookie 明文只存放在 **git 仓库之外**的 `~/.rd-cookies/`（可用环境变量 `RD_COOKIE_DIR` 覆盖）。
> 仓库内只含通用、可选的消费代码与文档，**不含任何 cookie 明文或私密清单**。

## 数据位置（仓库之外，绝不入库/推送）
```
~/.rd-cookies/
├── source/            # 明文 cookie 导出原稿（Windows 解密 JSON；600 权限）
├── vault.json         # 规范化统一 cookie 库（去重/剔除过期；600 权限）
├── netscape/<域>.txt  # 每注册域一份 Netscape Cookie File（yt-dlp/curl）
├── netscape/cookies.txt
└── make_vault.py      # 重建脚本（python3 标准库）
```
重建：`python3 /home/sensen/.rd-cookies/make_vault.py`

## 消费路径
1. **B 站 fast-path**（`web/task_engine.py::_exec_bilibili`）：
   命中 `.bilibili.com` 登录 cookie（SESSDATA/DedeUserID/bili_jct）→ BBDown 命令加 `-c <header>`；
   该 header 在**命令日志与 stdout 行**中统一 redact 为 `<cookie>`（`web/cookie_vault.py::redact`）。
2. **YouTube fast-path**（`_exec_youtube`）：命中 youtube 登录 cookie → yt-dlp 加
   `--cookies ~/.rd-cookies/netscape/youtube.com.txt`（路径不含密，可进日志）。
3. **DSH headless agent**（`_run_dsh_agent`）：透传 `RD_COOKIE_DIR` 环境变量；
   agent 走 CLI 桥 `node plugin/tests/e2e.mjs` 的 `fetch`/`download` 时，**默认对 vault 内的域
   自动注入 `Cookie` 头**（值只在内存，不进 stdout/日志）；显式 `"cookies":false` 可卸载保持匿名。
   agent 的决策与刷新命令见技能卡 `.dsh/skills/cookie-vault.md`。
4. rd-tools 插件若日后正式挂载，其 `http_fetch`/`download_file` 原生支持 `headers` 参数，可自行传 Cookie。

## 命令（agent / 用户均可调用，`~/.rd-cookies/vault_cli.py`）
```bash
python3 ~/.rd-cookies/vault_cli.py status        # 各域 cookie 数与剩余天数(min..max)
python3 ~/.rd-cookies/vault_cli.py has <domain>  # 0=有有效登录态
python3 ~/.rd-cookies/vault_cli.py source-list   # 列出 source/ 明文导出
python3 ~/.rd-cookies/vault_cli.py import <f>    # 导入新导出并重建
python3 ~/.rd-cookies/vault_cli.py refresh       # 从 source/ 重建 vault
```

## 匹配规则（web/cookie_vault.py 与 e2e.mjs 一致）
- cookie `domain` 以 `.` 开头（`.bilibili.com`）→ 匹配该域及其子域；
- 无点主机域（`www.x.com`）→ 仅精确匹配；
- session cookie 保留；已过 `expires` 的剔除。

## 失效与失败模式
- vault 缺失/坏 JSON → 引擎静默降级为无登录态现状，不阻断任务；
- cookie 过期（夸克有效期短，约 7 天）→ 任务如实报“登录态过期/需刷新”，并按 site-memory 记 `needs_login`；
- 禁止把 cookie 明文写入：任务日志、`web/tasks.db`、SSE 流、agent 输出、记忆、任何 git 跟踪文件；
- 严禁以任何理由向用户、会话交互或第三方透露任何 Cookie 明文或个人隐私细节。
