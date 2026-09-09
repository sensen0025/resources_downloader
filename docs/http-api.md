# HTTP API（非 Web 调用方式）

服务器同时提供 Web 控制台与一套 JSON HTTP API，脚本 / curl / 其它程序可直接调用。
无鉴权（有意设计：不引入密钥），但**每个 IP 都受提交冷却限制**（web 控制台同样受限，
因为每次提交都会消耗一次 DSH/LLM 运行）。

## 提交配额（per IP，持久化，重启不重置）
| 窗口 | 默认上限 | 环境变量覆盖 |
|---|---|---|
| 1 小时 | 10 次 | `RD_SUBMIT_HOURLY` |
| 1 天 | 25 次 | `RD_SUBMIT_DAILY` |
| 1 周 | 50 次 | `RD_SUBMIT_WEEKLY` |

超限返回 `429`，`detail` 含各窗口用量、上限、`retry_after_sec`。

## 端点速览
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/download` | 提交下载任务（受配额限制）；body 见下 |
| GET | `/api/v1/tasks` | 任务列表（含 `delivered_files` 产出清单） |
| GET | `/api/v1/tasks/{id}` | 单任务详情（含日志/产出） |
| GET | `/api/v1/tasks/{id}/events` | 单任务 SSE 事件流（进度实时） |
| GET | `/api/v1/tasks/events` | 全局 SSE 事件流 |
| POST | `/api/v1/tasks/{id}/cancel` | 取消排队/运行中任务（会 kill 其进程组） |
| GET | `/api/v1/files` | 文件库（home + repo 双根，见 `root` 字段） |
| GET | `/api/v1/files/download/{rel}?root=…` | 下载文件 |
| GET | `/api/v1/files/stream/{rel}?root=…` | 流式/断点（音视频播放） |
| GET | `/api/v1/probe?path=…` | 文件探针（大小/魔数/sha256） |
| GET | `/api/v1/system` | 系统状态（含本 IP 配额用量） |

## 提交请求体
```jsonc
{
  "url_or_query": "https://… 或 任意文字需求（如：下载《凡人修仙传》天星城壁纸）",
  "download_type": "auto",        // auto|youtube|bilibili|annas_archive|direct|hls|query
  "format_option": "best",        // best|audio_only|1080p|720p|epub|pdf …
  "output_name": "my_file.zip",   // 可选
  "subtitles": false
}
```

## curl 示例
```bash
BASE=http://服务器地址

# 1) 提交任务（受配额：1h≤10 / 1d≤25 / 1w≤50）
curl -X POST "$BASE/api/v1/download" \
  -H 'Content-Type: application/json' \
  -d '{"url_or_query":"https://www.youtube.com/watch?v=aqz-KE-bpKQ","format_option":"best"}'

# 2) 轮询任务到完成（检查 status=completed / delivered_files）
curl -s "$BASE/api/v1/tasks/<task_id>"

# 3) 取消
curl -s -X POST "$BASE/api/v1/tasks/<task_id>/cancel"

# 4) 看本 IP 剩余配额
curl -s "$BASE/api/v1/system" | python3 -m json.tool

# 5) 下载交付文件（root 取 delivered_files[].root / rel_path）
curl -L -o out.jpg "$BASE/api/v1/files/download/<rel_path>?root=repo"
```

## 建议的脚本节奏
1. 提交前先 `GET /api/v1/system` 看本 IP 配额；
2. 提交 → 429 则按 `detail.retry_after_sec` 等待；
3. 提交成功 → 轮询 `GET /api/v1/tasks/{id}`（或开 SSE）到 `status=completed/failed`；
4. 完成后读 `delivered_files[]`（含 role=primary/extra、root/rel_path）逐个下载。
