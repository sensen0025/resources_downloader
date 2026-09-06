# Site Skills 规范（资源站知识卡）

> 版本 1.0 · 2026-09-06 · 维护者：本仓库（sensen0025/resources_downloader）

## 0. 定位与原则

Site skill = 针对**某类/某个资源站**的**知识卡**（.md 文档），不是代码、不是常驻下载器。
通用能力（`resource-download` / `write-and-run-crawler` / `browser` / `run_code` /
`memory_*` 等）才是引擎；site skill 只负责"这个站：有什么、怎么拿、有什么坑、哪些方法实测过"。
站点改版 → 改知识卡（一行说明/接口路径），不重写仓库。

三条铁律：
1. **可复现优先**：凡"照做就能成功"的步骤必须标注验证状态（`✅` 实测 / `🧪` 未实测），
   实测要带日期与出口环境（例：`✅ 2026-09-06 海外出口`）。不标注=不可信，宁缺毋滥。
2. **不写死易碎细节**：选择器/正则/参数给"参考形态+验证日期"，写死反被站点当特征封。
3. **合规透明**：版权/ToS/反爬风险如实写，禁止把站做挂/批量滥用；付费/登录墙明确标出。

## 1. 文件与命名

- 位置：`.dsh/skills/site-<slug>.md`（slug 用小写 kebab，主域或服务名，如
  `site-annas-archive`、`site-huggingface-datasets`、`site-quark-netdisk`）。
- 一个站一个文件；跨站同类服务（学术 OA 全文）可按"一条链路"合并（见 `open-access-papers`）。
- frontmatter 只用 DSH 支持的键：
  ```yaml
  ---
  name: site-<slug>                 # 必须 ^[a-z0-9]+(-[a-z0-9]+)*$
  description: 一句话：这站能给什么资源、主流路径是什么、实测可用性
  whenToUse: 用户要的资源属于该站/该类型，或疑似该站镜像/同类站时
  ---
  ```

## 2. 正文固定章节（顺序不可乱）

1. **一句话定位**：这个站/服务是什么，给哪些资源类型。
2. **资源与格式**：常见可得资源与格式清单（例：epub/pdf/mobi、parquet、PDF、PNG 皮肤…）。
3. **访问路径（从稳到险排序）**
   1. 官方/开放接口（API / raw / 下载直链）
   2. 页面 → 直链/接口
   3. 镜像/备用域/聚合代理（列出已记录 mirror，来源标验证状态）
   4. 浏览器/登录/人工兜底（只在必要时）
4. **已知反爬/风控**（分条）：
   - 现象（403/429/CF/Turnstile/IP 风控/需要登录）
   - 本出口实测结论（日期），例如 `✅ 2026-09-06 数据中心出口：被 Akamai 拒`
   - 可行对策（换 UA/换镜像/走 API/要凭证…）
5. **工具速查**：可直接照做的命令/URL 模板/接口调用示例（`run_code` python、`http_fetch`、
   `download_file`、`browser solve/act`、`download_hls`），每条带 `✅/🧪` 与日期。
6. **验证与交付要点**：用什么探针（`probe_file` 魔数/大小）、交付字段（大小/sha256/来源）。
7. **合规/ToS**：版权状态、是否允许自动抓取、付费/登录墙位置。
8. **记忆建议**：本站在 `memory_remember` 里该写什么 tags（词表见 site-memory）与 methods 要点。

## 3. 资源站分类（"各大资源网站"的覆盖地图）

| 分类 | 覆盖 | 首批已建 |
|---|---|---|
| e-book / 电子书 | 版权书(Anna's…，仅工具性)、公版书(Gutenberg) | site-annas-archive、site-project-gutenberg |
| 学术 / OA 论文 | 出版社(出版商边缘 CDN)、开放镜像(Europe PMC)、定位服务(Unpaywall/Crossref) | site-open-access-papers |
| 数据集 | Hugging Face（公开/gated）、Kaggle(未建) | site-huggingface-datasets |
| 网盘 / 分享盘 | 夸克(需 Cookie/登录)… | site-quark-netdisk |
| 视频 / 流媒体 | B站(登录+IP 风控)… | site-bilibili |
| 小说 / 网文 | 笔趣阁系站点族… | site-biquge-novel |
| 皮肤 / 素材 | LittleSkin… | site-littleskin |
| 图片 / 壁纸 / 游戏资源 | haoWallpaper、GDGame、Minecraft schematics… | （待建） |

> 目录以 `.dsh/skills/site-directory.md` 为准；新增站点先入目录再写卡。

## 4. 质量门（提交前自查）

- [ ] frontmatter 合法；description ≤ ~200 字且含"给什么资源/稳不稳"。
- [ ] 每个可复现步骤有 `✅/🧪` + 日期；🧪 的写明"需在目标环境实测"。
- [ ] 没有站点专用可执行代码（只允许 URL 模板与短示例片段）。
- [ ] 合规风险、登录/付费墙位置写清。
- [ ] `site-directory.md` 已登记（分类/状态/日期）。

## 5. 生命周期

新建 →（按 spec 填卡）→ 部署环境实测打 ✅/修正 → memory 沉淀 → 站点改版/失效 →
改卡并更新目录状态（stale 标注），**不**新增常驻代码。
