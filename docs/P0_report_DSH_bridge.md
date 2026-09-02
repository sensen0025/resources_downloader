# P0 评估报告：Resource Hub × DSH HTTP 桥接骨架

日期：2026-09-02 · 范围：本地 Windows 先行闭环验证

## 结论

**HTTP 桥接方向成立**：DSH agent（或任意具备 shell/HTTP 能力的 agent）通过
`rh_bridge.py` / FastAPI `/api/v1/tasks` 即可发起资源任务并拿回文件，Python
确定性管线（检索/分析/直链下载/闸门/交付）原样执行。本地已验证三类闭环；
未发现需要推翻桥接架构的障碍。P1（插件固化）可启动。

## 实测数据

### 1. 本地 API 冒烟
- 启动：`python -m uvicorn api.app:app --host 127.0.0.1 --port 8000`
  （首次需删除旧版 `data/tasks.db` 重建 schema）。
- `GET /health` → `{"ok":true,"token_mode":false}`；建任务/轮询/取消/文件下载均 200。

### 2. 闭环用例
| 用例 | 输入 | 结果 | 耗时 |
|---|---|---|---|
| 纯 query（B站番剧） | 「凡人修仙传 第10集」无 seed | **failed**（搜索候选全为优酷/土豆付费墙、YouTube；Agent 4 次尝试全败） | ~10min |
| seed B站番剧页 | query + `seed_urls=[bangumi/play/ep733316]` | 枚举「10 限免」正确 → 下载失败（**DASH CDN 海外被拒**） | ~8min |
| seed littleskin 皮肤 | query「我的世界 银狼lv999 皮肤」+ `seed_urls=[littleskin.cn/skinlib/show/810649]` | **done**：`download_322573.png` 6789B，PNG 魔数正确，皮肤名闸门+安全扫描通过，文件 URL 实际下载字节一致 | ~1min |
| DSH 风格 agent 自主闭环 | 独立 subagent 仅凭 rh_bridge 用法说明 | **done**（task `d5bb7480ed0b`）：自主 fetch→轮询→下载验证，PNG 6789B 魔数 `89 50 4E 47`、SHA256 与报告一致 | ~2min |

### 3. 关键发现
1. **搜索结果强依赖出口 IP**：本地（海外）搜「凡人修仙传」→ 优酷/土豆/YouTube
   付费墙；服务器（国内）→ B站限免。→ 桥接架构下，**DSH agent 负责找源并把
   候选 URL 作为 seed_urls 传入**（确定性抓取），比让 Python 重新搜索更稳。
2. **B站 DASH CDN 地域限制**：海外 IP 能开 B站页、pgc API 能取流，但
   `cn-*.bilivideo.com` 国内 CDN 下载被拒/超时 → B站类抓取只在服务器（国内）
   环境验证。本地验证用 littleskin/GitHub 等全球 CDN 资源。
3. rh_bridge 错误处理正常（404/超时结构化 JSON，exit code 语义化）。
4. **独立 agent 可零引导闭环**：subagent 无本会话上下文、仅凭 rh_bridge 用法
   说明即完成 理解查询→桥接 fetch→轮询→实际下载验证→结构化报告 全流程，
   说明桥接接口对 DSH agent 足够"自解释"，P1 提示词/skill 只需薄薄一层。

## 体验与分工建议（对 P1 的输入）

- DSH agent 提示词应包含：①资源任务走 rh_bridge/API，不自己裸抓页面；
  ②有来源链接时优先 `--seed-url` 传入；③结果以 download_url 交付用户。
- 时间预算：本地直链类 ~1min；搜索型任务可能 5-10min（Agent 兜底慢）；
  B站类任务需服务器环境。
- 列表类（第N集/全部）意图表达：query 原样传入即可，Python `_is_list_intent`
  会识别；DSH 侧无需重复实现列表逻辑。

## P1 前置（下一阶段，另立任务）

1. 摸清 DSH cordis 插件注册 agent 工具机制（1 天验证；不支持则退回 skill 封装）。
2. `resource-hub-dsh-plugin`：resource_fetch / resource_status / resource_cancel。
3. 注册到 web profile → 重启 dsh web → 会话内工具级验收。
4. DOM 净化器（agent/dom_purify.py）作为独立并行任务（见主计划 P2）。
