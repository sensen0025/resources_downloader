---
name: cookie-vault
description: 本机私有登录态（Cookie Vault）管理器。管理 DSH 下载任务对用户个人登录态的“默认携带 / 按需卸载”决策，以及刷新/导入命令。【最高隐私红线】严禁以任何形式向用户、日志、对话或输出中透露 Cookie 明文或隐私数据！
whenToUse: 任务目标是登录墙/会员/需账号站点（vault 域列表内）；或某站请求带 cookie 后反而 403/风控/降级；或任务失败疑似登录态过期需要查看/刷新 cookie 时。涉及登录态时必须严守隐私禁令。
---

# cookie-vault（登录态管理与严控隐私）

> [!CAUTION]
> **【最高安全红线：绝对严禁透露任何 Cookie 隐私】**
> 1. **严禁明文透露**：任何 Cookie 的具体键值（如 `SESSDATA`、`token`、`SID`、`__pus` 等值）、Cookie 完整字符串、用户个人账号凭证、学业/学校相关信息，**绝对严禁**在任何用户对话、思考输出、任务日志、错误信息、环境探针或记忆中展示或透露！
> 2. **输出规范**：如需提及登录态，仅允许说明“已就绪 / 未就绪 / 已挂载用户登录态”，或统一使用 `<cookie>` 占位掩码，严禁输出任何真实的 Cookie 内容！
> 3. **学业隐私隔离**：加州大学（UC Berkeley、UCLA、UC Transfer 等）、SMC、Canvas LMS 等院校学业数据已被永久排除，严禁探测、读取或传输。

## 本机 cookie 是什么、在哪
- 位置：`~/.rd-cookies/`（环境变量 `RD_COOKIE_DIR` 可覆盖）——**git 仓库之外，永不提交/推送**。
- `vault.json` 统一库；`netscape/<域>.txt` 供 yt-dlp/curl；`make_vault.py` 重建；`vault_cli.py` 命令入口。
- 含用户个人登录态（如 bilibili SESSDATA、quark __pus、YouTube SID），是**用户隐私**。

## 默认策略：命中即带（默认加上）
- 引擎 fast-path：B 站 BBDown 自动 `-c <cookie>`（日志掩码 `<cookie>`）、YouTube yt-dlp 自动
  `--cookies netscape/youtube.com.txt`——**不用你额外操作**。
- agent 手动链路：目标 URL 的 host 落在 vault 域内时，**默认就带登录态**：
  `node plugin/tests/e2e.mjs fetch/download '{"url":"…"}'`（新版 e2e 默认注入，无需写 cookies:true）。
- 先查再打：`python3 /home/sensen/.rd-cookies/vault_cli.py has <domain>` → 0 表示有有效登录态。

## 按需卸载（不要无脑全带）
| 情形 | 动作 |
|---|---|
| 目标 host 不在 vault（第三方 CDN/对象存储/直链域名） | 不携带（默认自然不带，无需处理） |
| 带 cookie 反而被风控/403/降级（登录态+当前出口不匹配） | 显式重试匿名：`"cookies":false` |
| 仅需公开资源、根本不需要登录 | 保持匿名 `"cookies":false`，别暴露登录态 |
| 站点要求登录但 vault 无该域 | 走 site-* 卡：browser 登录一次（profile 持久）或如实告知用户需登录 |
- 卸载开关：e2e `fetch`/`download` 参数显式 `"cookies":false`；BBDown/yt-dlp fast-path 不带开关，但引擎只在 vault 命中时才注入，未命中天然匿名。

## 状态与刷新命令（agent 可直接 bash 调用）
```bash
# 概览：哪些域有 cookie、剩余天数（min..max）——用于判断登录态是否快过期
python3 /home/sensen/.rd-cookies/vault_cli.py status

# 单域判断：有没有有效登录态（exit 0=有）
python3 /home/sensen/.rd-cookies/vault_cli.py has bilibili.com

# 列出 source/ 里已有的明文导出文件
python3 /home/sensen/.rd-cookies/vault_cli.py source-list

# 导入一份新的 Cookie-Editor JSON 导出并重建库（自动从 source/ 重新生成 vault）
python3 /home/sensen/.rd-cookies/vault_cli.py import /path/to/export.json

# 仅重建（用户已把新导出放进 ~/.rd-cookies/source/）
python3 /home/sensen/.rd-cookies/vault_cli.py refresh
```

## 决策流程（登录墙站点）
1. `has <domain>`；有 → 按默认携带跑一次。
2. 带登录态失败（403/风控/Arg_KeyNotFound/登录态失效）→ 先试 `"cookies":false` 匿名一次，
   再失败 → `status` 看剩余天数：
   - 已过期/临期：跑 `refresh` 或 `import <新导出>`（需要用户先在源浏览器 Cookie-Editor 导出），
     仍不行 → 如实报“登录态过期，需用户刷新后重试”，并 memory 记 `needs_login` + 日期。
3. 任何情况下：cookie 明文与任何私密凭证不得进入你的回复、stdout、日志、任务输出、记忆或交付文本；严禁向用户或第三方透露任何具体的 Cookie 键值与个人凭据，提到登录态只说“已带/未带用户登录态（cookie vault）”。

## 隐私与合规红线
- 登录态属于用户个人私密财产，**严禁向任何对话方透露或回显**。
- 仅用于该用户授权的后台下载任务，不共享、不二次分发、不落库、不对外透露。
