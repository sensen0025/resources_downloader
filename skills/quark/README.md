# 夸克网盘自动解析技能(`skills/quark`)

把「用户复制夸克分享链接 → 手动打开 → 扫码/网页点击下载」变成全自动:

```
分享链接(pwd_id [+密码])  →  匿名拿 stoken → 递归列文件(含子目录)
  → 按意图选目标(过滤词/首选扩展名/最大文件)
  → 转存到自己网盘 → 轮询任务 → 换直链
  → 带夸克 UA/Cookie/Referer 下载落地 → 清理转存残留
```

实现对齐 [QuarkPan](https://github.com/lich0821/QuarkPan)、QuarkPanTool、musicdl 等
实测可用的官方 HTTP 接口链路 —— **纯 requests,零新增依赖,不需要浏览器,不需要用户扫码**。

## 为什么不再需要扫码

夸克分享下载必须「先转存到自己的网盘,再由自己的账号换直链」。
一次导入登录 Cookie(`__puus` 等)后:
- 每次响应里的 `Set-Cookie` 会自动合并回存储续期(对齐 Alist 的 `__puus` 刷新经验);
- 换直链与下载请求共用同一份 Cookie 快照 + 夸克客户端 UA,签名一致,避免 403。

**没有 Cookie 时仍可匿名浏览分享文件清单**(stoken 匿名获取),只是不能下载。

## 一次性导入 Cookie(二选一)

```bash
# 1) 环境变量(推荐,不落盘)
export QUARK_COOKIE="__puus=...; __pus=..."

# 2) 导入文件(存 accounts/quark_cookie.json)
python -m skills.quark auth import "__puus=...; __pus=..."
```

获取 Cookie:浏览器登录 pan.quark.cn → F12 → Network → 任意请求的 Cookie 头,
或 Storage → Cookies 里复制 `__puus`(夸克 App 扫码登录一次即可)。

## 命令行

```bash
python -m skills.quark auth status
python -m skills.quark list "https://pan.quark.cn/s/xxxx" [--password 1234]
python -m skills.quark download "https://pan.quark.cn/s/xxxx" --filter 投影 --out downloads
```

## Agent 技能(注册进注册表,AI 可直接调用)

| 工具 | 作用 | 需 Cookie |
|---|---|---|
| `quark_resolve` | 解析分享链接 → 返回文件清单(名称/大小/目录) | 否(可匿名) |
| `quark_download` | 解析 → 按意图选文件 → 转存 → 直链下载落地 | 是 |

`fetch_resource` 管线在直链无果时会自动对 pan.quark.cn 分享链接走 `quark_download`。

## 错误语义

- `QuarkAuthError`:需要 Cookie / Cookie 失效 → 提示一次性导入,不阻断其它候选;
- `QuarkError`:`密码错误 / 分享过期 / 转存失败 / 直链 403` → AI 换候选或换密码重试;
- 下载后默认清理转存残留(避免污染用户网盘)。

## 已知边界

- 转存需网盘容量(免费账号容量小,大文件可能转存失败);
- 分享需在有效期内;加密分享需正确密码(从链接 `?pwd=` 或『密码:xxx』自动提取);
- 夸克官方 API 可能变更,接口失效时本技能报错、由 AI 转其它源。
