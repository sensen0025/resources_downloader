---
name: site-bilibili-bbdown
description: 哔哩哔哩视频/番剧针对性下载：首选专用工具 BBDown（nilaoda）。单条/分P/番剧全集/指定画质/多编码，Cookie 或扫码登录提画质。需 ffmpeg 混流。别绕过本卡去现写 B站爬虫。
whenToUse: 用户要 B站(或 b23.tv/BV/ep/ss/合集/收藏夹/UP 空间)视频、番剧、音频、封面、弹幕，且本机已装或可装 BBDown 时。
---

# 哔哩哔哩 → BBDown

## 定位
B站针对性下载用 **BBDown**（免费、CLI、支持 Web/TV/App 接口、番剧/课程/合集/多分P/弹幕/字幕、
8K/HDR/杜比），社区持续适配。本卡是 B站请求的**首选路径**；通用卡 `site-bilibili` 作为降级知识。

## 安装与依赖
- 安装：`dotnet tool install --global BBDown`（需 dotnet）或从
  `github.com/nilaoda/BBDown/releases` 下 Linux release（`BBDown_<ver>_linux-x64.zip`）解压到 `~/bin`。
  更新：`dotnet tool update --global BBDown`。
- **本机已装**：`~/bin/BBDown`（v1.6.3 Linux x64，GitHub release 2024-08-14），`~/bin` 已加入 `~/.bashrc` PATH。
- 依赖：混流需 `ffmpeg`（已装，系统包 6.1.5）；没有会卡在合并步骤 → 用 `--skip-mux` 只拿分片。
- 检查：`BBDown --help`（✅ 2026-09-06 本机，输出 "BBDown version 1.6.3"）

## 鉴权前置
- 免费可下低画质；高清/会员内容需登录：`BBDown login`（扫码网页账号）或
  `-c "SESSDATA=…" <url>`（Cookie 串）。TV 无水印源：`BBDown logintv` / `-tv -token …`。

## 探测（先解析不下载）
```
BBDown -info "https://www.bilibili.com/video/BVxxxx"
BBDown --show-all "https://www.bilibili.com/video/BVxxxx"   # 多分P/合集看全部
```
确认能解析出画质列表再决定参数（解析失败=换 Cookie/出口，别直接开下）。

## 标准命令模板
```bash
# 单条默认(当前目录出 MP4)
BBDown "https://www.bilibili.com/video/BVxxxx"

# 指定目录+清晰度优先+编码优先+带 Cookie
BBDown -c "SESSDATA=…" -q "1080P 高码率,1080P" -e "hevc,avc" \
       --work-dir /path/to/downloads/bilibili "<url>"

# 番剧/合集全集 or 指定分P(1,2 / 3-5 / 10)
BBDown -p ALL "https://www.bilibili.com/bangumi/play/ssxxxx"
BBDown -p 3-5 "https://www.bilibili.com/video/BVxxxx"

# 只要音频 / 封面 / 弹幕(单独交付场景)
BBDown --audio-only "<url>";  BBDown --cover-only "<url>";  BBDown -dd "<url>"
```
输出默认在 `--work-dir`（未给则当前目录），多分P按 `<videoTitle>/…` 组织。

## 常见坑
- 无 ffmpeg → 卡混流：装 ffmpeg，或 `--skip-mux` 后说明交付的是音视频分片。
- 海外数据中心出口：网页/解析可能 OK，但下载 CDN/风控可能拒（见 site-bilibili 通用卡 ✅ 记录）→ 先 `-info` 再单分片试下，失败即如实报"需国内出口/登录态"。
- 接口/风控变更 → BBDown 版本过旧：先 `dotnet tool update` 再试；仍失败 → 降级通用流程并 memory 记录。

## 输出与校验
产物为 `.mp4`（默认已混流）→ `probe_file` 应见 mp4 `ftyp`、非空；交付 路径+大小+sha256+来源 BV/ss。

## 合规
BBDown 作者声明仅供个人学习研究非商业；会员内容须有对应账号权限；勿二次分发。

## 记忆建议
tags：`video` `bilibili` `bbdown` `login`；methods 记录：用的哪个接口/参数有效、海外出口卡点、失败降级日期。
