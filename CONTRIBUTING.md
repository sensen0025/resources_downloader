# 社区技能贡献指南 (Contributing to resources_downloader)

欢迎参与 `resources_downloader` 技能库与工具生态的建设！
我们致力于让 **DSH 智能体拥有自主应对任意资源站点下载的能力**。

---

## 核心设计原则（贡献前必读）

1. **知识入卡，通用入底座（零站点专有常驻代码）**：
   - 站点专有的参数、URL 规则、过盾手法、反爬坑点，**必须写在 `.dsh/skills/site-*.md` 技能卡中**，让 LLM 现场阅读并执行。
   - `plugin/` 工具插件仅维护与具体站点无关的通用执行底座（下载、HLS、探针、浏览器自动化等）。**严禁向 `plugin/` 添加特定站点的写死代码**。
2. **成熟工具优先**：
   - 某站点如果有活跃、成熟的开源下载器（如 B站 BBDown、YouTube yt-dlp、图站 gallery-dl 等），请编写**专用下载器技能卡**（命名：`site-<scope>-<tool>.md`），**禁止绕过成熟工具手写临时爬虫**。
3. **可复现与证据驱动**：
   - 技能卡中的每条命令或调用示例，必须实测并标注状态与日期（例：`✅ 2026-09-06 本机实测`；若未完全跑通需标 `🧪 未实测(原因)`）。
4. **探针强制校验**：
   - 任何技能流程必须包含产物探针校验（魔数/文件大小/哈希）。

---

## 贡献流程

```
Fork 仓库 ──► 新建分支 ──► 编写技能卡/更新目录 ──► 本地离线单测 ──► 提交 PR
```

### 第一步：确定技能分类与命名规范

| 分类 | 命名规则 | 对应规范 | 示例 |
|---|---|---|---|
| **专用下载器卡** | `.dsh/skills/site-<scope>-<tool>.md` | [`docs/targeted-downloader-spec.md`](./docs/targeted-downloader-spec.md) | `site-bilibili-bbdown.md`, `site-videos-yt-dlp.md` |
| **通用站点知识卡** | `.dsh/skills/site-<slug>.md` | [`docs/site-skills-spec.md`](./docs/site-skills-spec.md) | `site-annas-archive.md`, `site-project-gutenberg.md` |
| **通用方法论卡** | `.dsh/skills/<method-name>.md` | 通用流程方法论 | `resource-download.md`, `download-and-verify.md` |

### 第二步：按模板编写技能卡

#### 1. Frontmatter 规范 (YAML)
```yaml
---
name: site-<slug>
description: 一句话描述：该站点能提供什么资源、主流路径是什么、实测可用性与前置依赖。
whenToUse: 明确触发条件：用户提到该站点/格式/需求且命中该场景时激活。
---
```

#### 2. 正文固定章节结构
- **一句话定位**：服务与资源定位。
- **资源与格式**：产物清单与文件扩展名。
- **访问路径（从稳到险排序）**：API/直链 ➔ 页面解析 ➔ 镜像代理 ➔ 浏览器/过盾。
- **已知反爬/风控与对策**：CF / DDoS-Guard / IP 限制 / 登录态实测结论与绕过手法。
- **工具速查与命令模板**：可直接供 LLM 消费的命令或短脚本。
- **输出与探针校验**：魔数（如 MP4 `ftyp`、EPUB `PK`、PDF `%PDF`）、大小与哈希核对。
- **合规与版权**：个人学习与公有领域边界说明。
- **记忆建议**：推荐的 `memory_remember` tags 与 methods 记录方式。

### 第三步：同步更新总目录

在 [`.dsh/skills/site-directory.md`](./.dsh/skills/site-directory.md) 的对应表格中新增或更新一行记录：
- 专用下载器填入 **A 表**；
- 站点知识卡填入 **B 表**；
- 状态栏更新为 `✅`（已实测）或 `🧪`（待实测）。

---

## 提交质量门禁 (PR Checklist)

在发起 Pull Request 之前，请确认已勾选以下检查项：

- [ ] Frontmatter 格式合法，`name` 保持小写短横线命名，`description` 语义清晰。
- [ ] 提供了实测环境与日期标注（`✅ YYYY-MM-DD 出口环境` 或 `🧪`）。
- [ ] 没有任何特定站点的常驻死代码注入 `plugin/`。
- [ ] 包含基于 `probe_file` 或文件头魔数的校验流程。
- [ ] 已在 `.dsh/skills/site-directory.md` 登记并更新状态。
- [ ] 本地离线单测全部通过：
  ```bash
  node plugin/tests/run.mjs
  for f in plugin/index.js plugin/lib/*.js; do node --check "$f"; done
  ```

---

## 社区行为准则与合规声明

- 严禁提交用于对任何目标站点实施拒绝服务（DDoS）、恶意滥用、暴力破解或绕过付费墙抓取受限资产的脚本或指令。
- 技能卡仅供个人离线学习、技术探索与公有领域文献视听归档。
- 感谢所有开源社区贡献者为智能体自动化能力建设做出的贡献！
