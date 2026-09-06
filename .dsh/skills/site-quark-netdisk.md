---
name: site-quark-netdisk
description: 夸克网盘(pan.quark.cn)分享资源：分享链接→递归清单→定位目标文件→需夸克登录态(Cookie)转直链下载。无 Cookie 时如实告知需要登录态；纯 HTTP 一般被挡在登录/风控层。
whenToUse: 用户给 pan.quark.cn/s/xxx 分享链接要下载其中文件，或说"夸克网盘的资源"。
---

# 夸克网盘（pan.quark.cn）

## 定位
夸克网盘分享 = 页面级反爬 + 登录态要求；官方无公开匿名下载 API。**能不能拿全看有没有登录 Cookie**。

## 资源与格式
任意文件类型（压缩包/文档/视频/软件…），分享带目录结构，常需"选文件"而非整包。

## 访问路径（先要凭证，再谈解析）
1. ✅ 凭证现成：私有 cookie vault（`~/.rd-cookies`，2026-09-06 自 Windows Edge 解密导入，含
   `__pus/__uid/__kp` 等夸克登录态，⚠️ 有效期短（约 7 天），失效即如实报"需刷新登录态"）。
   agent 侧：CLI 桥 `fetch`/`download` 默认自动带夸克登录态（显式卸载用 `"cookies":false`）；
   或 `browser open pan.quark.cn`（profile 持久，登录一次后自动带态）。
2. 拿链接清单：带 Cookie 请求分享页/其接口 → 递归目录 JSON → 定位目标文件 id/名称。
3. 取下载直链：对目标文件调转存/直链接口（需登录态），拿到真实下载 URL。
4. `download_file` 落地（可带 Referer: https://pan.quark.cn/ 与 Cookie）。

## 已知风控
- 无 Cookie：基本止步登录/风控页（✅ 通用规律，未经本站实测）。
- 有 Cookie：接口易变，具体路径按"当时抓到的请求"写爬虫（write-and-run-crawler），并 memory 记录可用接口形态（日期）。
- ⚠️ cookie 属个人隐私：不得打印明文进日志/任务输出；vault 匹配不到夸克登录态时如实说明需要刷新。

## 工具速查
- 登录态：`browser open pan.quark.cn` → 用户手输/扫码 → `cookies` 导出 → 后续 `http_fetch`/`download_file` 带 `Cookie:` 头。
- 🧪 所有解析/直链路径待有登录态环境实测后打 ✅。

## 验证与交付
`probe_file` 验文件（zip `PK`/pdf `%PDF`/媒体魔数）；交付 文件名+大小+sha256+来源分享链接。

## 合规
仅下载用户有权访问的分享内容；登录态属用户个人账号，勿共享/勿滥用。

## 记忆建议
tags：`pan` `login` `cookie`；methods 记录 Cookie 获取方式与可用接口（带日期），无凭证环境别反复撞登录墙。
