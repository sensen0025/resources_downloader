---
name: site-directory
description: 资源站技能总目录：专用下载器卡(A 表，首选路径) + 通用知识卡(B 表) + 待建队列。处理具体站点资源先查这里：有专用下载器就用它，其次知识卡，最后通用流程。状态：✅ 已实测 / 🧪 未实测。规范见 docs/site-skills-spec.md 与 docs/targeted-downloader-spec.md。
whenToUse: 用户要的资源属于某站点/某类型，需要判断"这个站怎么拿最稳（专用工具？知识卡？）"时；或新增站点/工具前先登记。
---

# site-directory（资源站技能目录）

> 通用卡规范 `docs/site-skills-spec.md`；专用下载器规范 `docs/targeted-downloader-spec.md`。
> 状态：✅ 已实测 / 🧪 未实测（需目标环境验证）。

## A. 专用下载器（首选路径——某站有专用工具就走它）

| 站点族 | 首选工具 | site 卡 | 状态 |
|---|---|---|---|
| 哔哩哔哩 | **BBDown**(nilaoda, 需 ffmpeg) | site-bilibili-bbdown | ✅ v1.6.3 已装(~/bin)，ffmpeg 6.1.5 已装，2026-09-06 本机实测可运行 |
| YouTube / 千站视频 | **yt-dlp**(需 ffmpeg + node) | site-videos-yt-dlp | ✅ 已装(~/bin)，Node/ffmpeg 就绪，2026-09-06 本机实测完整解析与下载 |
| 国内长视频(优酷/爱奇艺/芒果/腾讯) | lux 或 yt-dlp | site-cn-video-lux | 🧪 待建卡 |
| 图站(twitter/instagram/pixiv/danbooru…) | gallery-dl | site-gallery-dl | 🧪 待建卡 |
| 音乐(Spotify/YT Music) | spotdl / yt-dlp | site-music-spotdl | 🧪 待建卡 |

## B. 通用知识卡（无专用工具时的降级知识/链路）

| 分类 | site 技能 | 覆盖 | 状态 |
|---|---|---|---|
| 学术/OA | site-open-access-papers | Crossref→MDPI→Unpaywall→Europe PMC | ✅ 链路(出版商直连被 IP 拒) |
| 数据集 | site-huggingface-datasets | HF：公开/gated/流式取样 | ✅ |
| e-book | site-project-gutenberg | 公版书 txt/epub 直链 | ✅ |
| e-book | site-annas-archive | Anna's Archive (https://annas-archive.gd/ + DDoS-Guard 过盾 + 慢速合作节点直链下载) | ✅ 2026-09-06 本机实测 |
| 素材/皮肤 | site-littleskin | Yggdrasil+Mojang 备路 | ✅ 备路 |
| 网盘 | site-quark-netdisk | 夸克分享(需登录 Cookie) | 🧪 |
| 视频/流 | site-bilibili | B站风控/出口知识（降级用；首选见 A 表 bbdown） | 🧪 |
| 小说 | site-biquge-novel | 笔趣阁站群套路 | 🧪 |

## 怎么用
1. `memory_query` 查域（记忆可能比卡更新鲜）。
2. 查 A 表：有专用下载器 → 加载对应卡照做（不绕开它去写爬虫）。
3. 无专用工具再查 B 表知识卡；仍无 → `resource-download` 通用流程，结束后 `memory_remember`。
4. 卡状态 🧪 ≠ 不可用：按卡内步骤试，实测结果回写（改 ✅ / 更新卡 / 更新记忆）。
5. 新增站点或工具：先在对应表加行（含许可证与用途边界），再按对应规范建卡。
