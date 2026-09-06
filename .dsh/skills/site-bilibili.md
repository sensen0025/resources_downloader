---
name: site-bilibili
description: B站(bilibili.com) 番剧/视频：页面/接口可取流清单，但下载被登录态、IP 属地(海外拒绝国内 CDN)、风控三重卡。本卡只做"正确姿势"知识，不做稳定保证；本环境(数据中心海外出口)实测不可下。
whenToUse: 用户要 B站某视频/番剧/音频，或给 bilibili/b23.tv 链接；需要明确"能不能拿、卡在哪"。
---

# B站（bilibili）

## 定位
**首选路径：专用下载器 BBDown —— 见 `site-bilibili-bbdown` 卡（先加载它，别绕开现写爬虫）。**
本卡只做降级知识：B站内容分网页/接口层（可探测）与 CDN 下载层（强风控）。海外出口基本拿不到 DASH 国内 CDN。

## 资源与格式
视频（DASH 分音视频流）、番剧分集（列表意图：全集/第 N 集）、封面图、弹幕(不展开)。需区分 UP 主视频(BV)与番剧(ep/ss)。

## 访问路径
1. 元数据/清单：页面 `https://www.bilibili.com/video/BV…` 或 `bangumi/play/ep…`；播放信息接口需登录态 Cookie（`SESSDATA`），游客态限流明显。
2. 列表意图：`第 N 集/全部` → 番剧页枚举分集 → 按目标集取其 ep id（写爬虫或人工浏览）。
3. 取流：pgc/playurl 接口返回 DASH 音视频分片 URL。
4. 下载：
   - ✅ 2026-09-06（海外数据中心出口，历史结论）：页面可开、接口可取流，但 `cn-*.bilivideo.com` DASH CDN 对海外 IP 拒绝/超时 → **本出口下不了**
   - 国内出口 + 登录态：可 `download_hls`/分片拉流（🧪 未在本地实测；需国内 IP 环境）
5. 兜底：换海外友好源/告知用户需国内网络与账号。

## 已知风控
- 未登录游客限流、部分内容需大会员
- IP 属地风控（海外拒 DASH CDN）——先判出口再决定是否投入
- `wbi` 签名等反爬参数 → 写爬虫时从页面 JS 现场取，别写死

## 工具速查
- 判断出口是否可行：先抓一条分片 URL 试 `download_file`/`http_fetch` Range；海外拒 → 立即停，转兜底。
- 需登录态：`browser` 让用户登录一次 → `cookies` 导出 `SESSDATA` → 回灌接口请求。

## 验证与交付
能落地才交付（DASH 音频+视频需合并，未装 ffmpeg 时如实说明）；否则交付"清单+卡点证据(哪个 CDN 拒)"。

## 合规
遵守 B站用户协议：仅个人缓存、不二次分发；大会员内容需有权限。

## 记忆建议
tags：`video` `login` `ip-blocked` `cdn-region` `dash`；methods 记录出口结论与时间，避免海外反复试。
