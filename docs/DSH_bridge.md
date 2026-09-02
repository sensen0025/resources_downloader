# Resource Hub × DeepSeek Harness 桥接（P0）

> 定位：DSH agent 是"大脑与外壳"（AI 理解/规划/多轮对话），Resource Hub
> （Python FastAPI）是"执行层"（检索/分析/确定性下载/安全与内容闸门）。
> 两边通过 HTTP `/api/v1/tasks` 桥接，Python 技能栈（355+ 回归测试、
> bilibili/quark/novel/streaming/security、登录态、站点信誉库）**全部保留**。

## 为什么桥接而不是重写

| | DeepSeek Harness (Node) | Resource Hub (Python) |
|---|---|---|
| 强项 | agent 编排、subagent/workflow、Web GUI、会话持久化 | 确定性下载管线、浏览器自动化+登录态、DASH/pgc 抓流、魔数/安全扫描闸门、站点信誉库 |
| 弱项 | 无任何下载/浏览器技能 | 自研简化 agent-loop（最弱一环） |

分工：DSH 负责"找源 + 理解意图 + 规划多步"；Resource Hub 负责"拿文件"。
Node 重写下载技能会丢弃全部积累，不可取。

## 现状（2026-09-02 实测）

- 本地：`python -m uvicorn api.app:app --host 127.0.0.1 --port 8000`（win，Python 3.11）。
  首次启动需删除旧版 `data/tasks.db`（旧 schema 无 owner 列）让它重建。
  token_mode=false → 匿名按 IP 租户建任务。
- 服务器（175.155.64.171:8000 / 公网 31730）：同代码，国内出口 IP。
- **搜索环境差异（重要）**：本地（海外出口 IP）搜「凡人修仙传 第10集」返回
  优酷/土豆付费墙、YouTube；服务器（国内出口）返回 B站番剧限免集。
  结果强依赖出口 IP → **DSH agent 自带找源能力时，把候选 URL 作为
  `seed_urls` 传给 Resource Hub 更可靠**（确定性抓取，不赌搜索环境）。
- **B站 CDN 地域限制**：本地海外 IP 打开 B站页面正常、pgc playurl API 可取到流，
  但 DASH 国内 CDN 节点（cn-*.bilivideo.com）对海外 IP 下载被拒/超时 →
  B站类资源抓取只在服务器（国内）环境验证；本地验证用无地域限制资源
  （littleskin/GitHub 等全球 CDN）。

## 桥接封装：rh_bridge.py（DSH agent 一条命令拿结构化结果）

```
python rh_bridge.py fetch   "我的世界 银狼lv999 皮肤" --seed-url https://littleskin.cn/skinlib/show/810649 --base http://127.0.0.1:8000 --timeout 600
python rh_bridge.py submit  "<查询>" [--seed-url ...]     # 只提交,返回 task_id
python rh_bridge.py poll    <task_id> [--timeout 300]
python rh_bridge.py files   <task_id>                      # 文件与 download_url
python rh_bridge.py cancel  <task_id>
```

输出一律 JSON。fetch 到终态打印：
`{task_id, status, success, summary, error, files:[{name,size,sha256,verdict,download_url}], sources, pan_links}`。

文件下载 URL 直接可 GET（带任务 file_token）：
`GET {base}/api/v1/tasks/{task_id}/files/{name}?token={file_token}`

## P0 实测记录

1. 本地冒烟（无 seed，纯 query「凡人修仙传 第10集」）：搜索候选被付费墙占领
   （优酷/土豆/YouTube），4 次 Agent 尝试全败 → 结论：本地不赌搜索，用 seed_urls。
2. 本地 seed B站番剧页（ep733316 + query 第10集）：枚举出「10 限免」正确，
   但 DASH CDN 海外下载失败 → 结论：B站抓取限服务器环境。
3. 本地 seed littleskin 皮肤详情页（query「我的世界 银狼lv999 皮肤」+
   seed `https://littleskin.cn/skinlib/show/810649`）：**done**，约 1 分钟。
   - 直链 raw/810649 → download_322573.png（6789 B, PNG 魔数正确）
   - 皮肤名匹配闸门通过（来源页标题含 银狼lv999）
   - 安全扫描通过（unknown=无杀毒引擎,启发式放行）
   - 文件经 `/files/{name}?token=` 实际下载验证字节一致
4. DSH 风格 agent（独立 subagent，仅凭 rh_bridge 用法说明）自主完成同一任务
   → 结果见 subagent 报告（成功/失败均记录）。

## 下一步（P2 等，见下）

- **P1 已完成（2026-09-02）**：resource-hub-dsh-plugin（独立仓库
  `C:\Users\sense\Desktop\1\resource-hub-dsh-plugin`，commit 1da7a4b）。
  - cordis 插件 + `dsh-tools.defineTool`，零运行时依赖（Node 内置 fetch）。
  - 工具：`resource_fetch`（wait=true 阻塞 / wait=false 提交）、`resource_status`、
    `resource_cancel`；`ctx.systemPrompt.section` 注入用法提示。
  - 注册：`dsh plugin --profile headless add "link:.../01_content"`（web 同理）。
  - 实测（headless profile，真实下载闭环）：
    ① 阻塞模式皮肤下载 done；② wait=false + resource_status 轮询 done
    （期间修复 resource_status schema 漏声明 sources/pan_links 的 bug）；
    ③ 批量两个皮肤任务均 done。
  - 注意：`ctx.setInterval` 需 inject `timer`（cordis-plugin-timer），已移除自检。
- **P2 DOM 净化器**：独立低风险，见主计划（agent/dom_purify.py + 感知/枚举器接入）。
- **web profile 注册**：`dsh plugin --profile web add "link:.../01_content"` +
  重启 dsh web（会短暂中断 GUI 会话）——待用户确认后执行。
- **服务器部署**：Node ≥20 + `npm i -g @deepseek-ai/dsh` + profile + 端口映射；
  baseUrl 指到服务器 API。
