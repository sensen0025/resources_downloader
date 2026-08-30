# Resource Hub — 流式下载技能(`skills/streaming`)

专治**音视频等大文件**:直链分段并发、HLS(m3u8) 流、边下边交付、限速、进度上报。
与 `delivery/downloader`(整文件 + 单流续传)互补 —— 大媒体自动分流到这里。

```
probe(HEAD) ──┬─ is_hls ──→ HLS: master/variant → 分段并发下载(AES-128 可选) → 按序合并
              └─ direct ──┬─ 支持 Range 且 ≥5MB → 分片并发(分片级断点续传)
                          └─ 否则 → 单流顺序 + 边下边交付(.part 续传)
```

## 用法(CLI)

```bash
# 探测 URL:大小 / Range / m3u8 / 媒体类型
python -m skills.streaming.cli probe https://example.com/video.mp4

# 流式下载(自动识别直链/HLS)
python -m skills.streaming.cli download https://example.com/video.mp4 --out downloads
python -m skills.streaming.cli download https://.../index.m3u8 --json
python -m skills.streaming.cli download URL --segments 8 --limit 2M   # 并发 8 / 限速 2MB/s
```

## 作为 Agent 技能怎么用(核心 API)

```python
from skills.streaming import probe_stream, stream_download

p = probe_stream("https://example.com/movie.mkv")
print(p.content_length, p.accept_ranges, p.is_hls)   # 决定策略

r = stream_download(
    "https://example.com/movie.mkv", "downloads",
    segments=4,                        # 分段并发
    on_progress=lambda done, total: print(f"{done}/{total}"),  # 实时进度
    on_chunk=lambda chunk: ...         # 边下边交付(逐块回调,可接流式响应)
)
print(r.ok, r.strategy, r.path, r.size, r.sha256)
```

Agent 侧:工具 `download_stream` 已在 `ai/skills.py` 注册;`fetch_resource`
`stream_media=True`(默认)时,音视频类型(mp4/mkv/mp3/flac/m3u8 等)自动走流式下载,
进度通过 `on_stage("download", "...")` 上报(API 的 SSE 里能看到实时百分比)。

## 三种策略

1. **direct-segments(直链分段并发)** — 探测到 `Accept-Ranges: bytes` 且文件 ≥5MB 时,
   按 Range 切成 N 段多线程下载(`--segments`,默认 4,上限 16),完成后按序合并;
   **分片级断点续传**:已完整分片自动跳过(aria2/yt-dlp 同款思路);
2. **direct-single(单流 + 边下边交付)** — 不支持 Range 或小文件,单流顺序下载,
   `.part` 断点续传,`on_chunk` 可逐块交付给调用方;
3. **hls(m3u8)** — 解析 master 播放列表(自动选带宽最高的 variant)→ 分段并发下载
   (上限 8)→ 按序合并;支持 AES-128 加密分段(`EXT-X-KEY`,需 `cryptography`,
   IV 缺省 = 分段序号,符合 HLS 规范)。

## 诚实边界

- **fmp4(`#EXT-X-MAP`)无法纯拼接**:返回 error 提示需 ffmpeg(`ffmpeg -i playlist.m3u8 out.mp4`);
- 加密分段无 `cryptography` 时明确报错,不静默产出损坏文件;
- 代理:统一走 `proxy.py`(RH_PROXY_URL),与全项目一致。

## 测试

```bash
python -m unittest tests.test_streaming -v
```

覆盖(本地起 ThreadingHTTPServer,不依赖外网):Range 分片下载 sha256 一致、分片级续传、
单流限速、进度回调单调、HLS 明文合并、HLS AES-128 加密合并、probe 字段、工具注册。
