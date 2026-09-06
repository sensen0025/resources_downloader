---
name: site-videos-yt-dlp
description: YouTube 及千站通用音视频下载：首选专用工具 yt-dlp。支持 YouTube(单个/播放列表/频道/Shorts/直播/音频提取/内嵌字幕/分段切片)、以及 Twitter/TikTok/Vimeo 等数千站点。支持 Cookie 登录与画质/编码优先级选择。需 ffmpeg 与 node 环境。✅ 2026-09-06 本机实测完整闭环。
whenToUse: 用户需要下载 YouTube（或 youtube.com/youtu.be/shorts/playlist/channel）视频、音频、字幕、缩略图，或其它主流海外视频站且无需专有逆向时。
---

# YouTube / 通用视频 → yt-dlp

## 定位
YouTube 视频/音频/字幕检索与下载首选 **yt-dlp**（活跃维护、全平台 CLI、支持 YouTube 及全球 1000+ 流媒体与音视频站）。
它自动处理客户端 API 签名解密、格式流分离（DASH 音视频合并）、多语言字幕抽取与重试。

## 安装与依赖
- **安装**：
  - 二进制：从 `github.com/yt-dlp/yt-dlp/releases` 下载 Linux 独立单文件到 `~/bin/yt-dlp` 并 `chmod +x`。
  - Python 包：`pip install --user -U yt-dlp`（或 `pipx install yt-dlp`）。
  - 更新：`yt-dlp -U`。
- **本机已装**：`~/bin/yt-dlp`（版本 `2026.08.19` 及以上），`~/bin` 已加入 PATH。
- **核心依赖**：
  - `ffmpeg` ✅（已装 6.1.5，用于 DASH 音视频流合流为 MP4/MKV、格式转换）。
  - `node` ✅（已装，作为 `--js-runtimes node` 执行 YouTube 播放器解密，避免提取警告与缺失格式）。
- **检查命令**：`yt-dlp --version`（✅ 2026-09-06 本机实测输出版本号）。

## 鉴权前置
- **公开视频**：直接匿名拉取最高画质（最高可达 4K/8K 60fps AV1/VP9）。
- **会员视频 / 年龄限制 / 私享列表**：
  - ✅ 本机已具备 YouTube 登录态（私有 cookie vault `~/.rd-cookies`，2026-09-06 导入，含
    `SID/SSID/LOGIN_INFO` 等）——任务引擎的 yt-dlp fast-path 自动挂 `--cookies ~/.rd-cookies/netscape/youtube.com.txt`；
    agent 手动跑时直接 `--cookies ~/.rd-cookies/netscape/youtube.com.txt`。
  - 导出浏览器 Cookie 文件：`yt-dlp --cookies /path/to/cookies.txt "<url>"`
  - 自动读取浏览器 Cookie：`yt-dlp --cookies-from-browser chrome "<url>"`（或 `firefox` / `brave` 等）
  - OAuth 登录（电视端授权）：`yt-dlp --username oauth --password '' "<url>"`（终端提示扫码/输验证码）
- ⚠️ cookie 属个人隐私：路径本身可写进命令，**明文值不得打印进日志/任务输出**。

## 探测（先解析不下载）
```bash
# 1. 探测可用画质、编码与音轨列表
yt-dlp --js-runtimes node -F "https://www.youtube.com/watch?v=VIDEO_ID"

# 2. 仅获取视频元数据 JSON（不下载文件）
yt-dlp --js-runtimes node --dump-json "https://www.youtube.com/watch?v=VIDEO_ID"

# 3. 探测播放列表目录（平铺列出所有条目标题和 ID，不解析内层流）
yt-dlp --flat-playlist --dump-json "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

## 标准命令模板

```bash
# 1. 单条标准下载（画质优先：最高 1080P/4K + 最佳音频，自动混流为 MP4）
yt-dlp --js-runtimes node \
  -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best" \
  --merge-output-format mp4 \
  -o "/home/sensen/Downloads/youtube/%(title)s.%(ext)s" \
  "<url>"

# 2. 只要最高音质音频（转换为 MP3 / M4A）
yt-dlp --js-runtimes node \
  -x --audio-format mp3 --audio-quality 0 \
  -o "/home/sensen/Downloads/youtube/%(title)s.%(ext)s" \
  "<url>"

# 3. 带字幕下载（自动字幕+人工字幕，中文/英文，嵌入或独立 .vtt/.srt）
yt-dlp --js-runtimes node \
  --write-subs --write-auto-subs --sub-lang "zh-Hans,zh,en" --convert-subs srt \
  -o "/home/sensen/Downloads/youtube/%(title)s.%(ext)s" \
  "<url>"

# 4. 精确时间分段下载（免下全片，秒级切片）
yt-dlp --js-runtimes node \
  --download-sections "*00:01:30-00:03:00" \
  --merge-output-format mp4 \
  -o "/home/sensen/Downloads/youtube/%(title)s_clip.%(ext)s" \
  "<url>"

# 5. 播放列表批量下载（带序号与防封节流）
yt-dlp --js-runtimes node \
  --playlist-items 1-10 \
  --sleep-interval 2 --max-sleep-interval 5 \
  -o "/home/sensen/Downloads/youtube/%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s" \
  "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

## 常见坑
1. **未配置 JavaScript Runtime**：
   - 报错/警告：`No supported JavaScript runtime could be found`，可能导致部分格式丢失或被限速。
   - 对策：命令务必附带 `--js-runtimes node`（本机 Node 已在 PATH）。
2. **403 Forbidden / SABR 速度限制**：
   - 现象：下载分片返回 403 或只有几 KB/s。
   - 对策：更新 yt-dlp 到最新版本（`yt-dlp -U`）；必要时增加 `--extractor-args "youtube:player_client=ios,web"` 或挂载 `--cookies`。
3. **无 ffmpeg 导致音视频无法合并**：
   - 现象：下载出单独的 `.f396.mp4` 和 `.f251.webm`。
   - 对策：本机系统 ffmpeg 6.1.5 已就绪，默认使用 `--merge-output-format mp4` 自动合流。
4. **长标题与特殊字符文件名冲突**：
   - 对策：使用 `--windows-filenames` 或 `--restrict-filenames` 防止非法字符截断。

## 输出与校验
- **产物文件**：默认输出为 `.mp4`（视频）或 `.mp3`/`.m4a`（音频）。
- **探针验证**：
  - MP4 格式：魔数 `66747970`（`ftyp`）、非空且大小符合预期。
  - WebM 格式：魔数 `1a45dfa3`。
  - MP3 格式：魔数 `494433`（`ID3`）或 `fffb`。
- **交付元数据**：视频标题、时长、清晰度/分辨率、最终文件大小 (MB)、SHA-256 哈希值、YouTube 原始链接。

## 合规
- 遵守 YouTube 服务条款及创作者版权规定；
- 仅用于个人离线学习、归档研究与公有领域视听资料收集，严禁商业分发与侵权扩散。

## 记忆建议
- tags：`video` `youtube` `yt-dlp` `audio` `subtitles` `playlist` `ffmpeg` `nodejs`
- methods：执行命令必须携带 `--js-runtimes node`；混流固定使用 `--merge-output-format mp4`；长列表必须加入 `--sleep-interval` 避免触发频控。
