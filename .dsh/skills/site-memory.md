---
name: site-memory
description: 站点/服务记忆与打分。对处理过的网站、镜像、数据集、网盘、服务端（含反爬、封禁、登录墙等）做持久记录并打分（1–5），下次遇到同一域或要找“类似网站”时先查记忆再动手。工具：memory_remember（写/合并打分）、memory_query（精确/别名/标签/类型查询 + similar 相似站排序）。
whenToUse: 每次资源请求开始前；遇到新站点/镜像/数据集并已得到结论后；需要“还有哪些类似网站/镜像可试”、想避开已知死路、或不确定某站是否已被验证过时。
---

# site-memory（站点记忆与打分）

不要每次都从零摸一个站。把每次交手结论沉淀下来，让下一次（或相似站点）直接赢在起跑线上。

## 什么时候写（任务节奏）
- **开始时**：`memory_query` 查该域（精确+别名）与 `similar: true`（相似站）；
- **过程中**：镜像/别名/反爬特征/新接口一出现就值得记；
- **结束时**：对接触过的每个域 `memory_remember` 打分收口（成功与失败都记——**失败记录最值钱**）。

## 打分口径（score 1–5）
| 分 | 含义 |
|---|---|
| 5 | 直连就能下，稳定（如 GitHub raw / 官方 CDN） |
| 4 | 偶需换 UA/接口，整体可靠 |
| 3 | 能用但要绕（限流/需解析/部分文件可下） |
| 2 | 时好时坏、重反爬、经常失效 |
| 1 | 此出口基本不可用（Akamai/IP 封、Turnstile、停放域） |

verdict 与分对应：`ok / partial / blocked / needs_login / unknown`。

## 字段怎么填（memory_remember）
- `domain`：域名（自动去 http/www/路径）；数据集写成 `huggingface.co/datasets/sander-wood/m4-rag`。
- `aliases`：同服务的其它域名（镜像/主域互挂），如 annas 各镜像互相列；这样查任何一个都能命中全部。
- `tags` 常用词表：`mirror` `antibot` `cf` `akamai` `turnstile` `ip-blocked` `ratelimit` `gated` `login` `open-access` `dataset` `api` `redirect` `parked` `dns-fail` `js-render`。
- `resource_types`：`ebook` `pdf` `dataset` `image` `audio` `video` `pan` `archive` `software`。
- `methods`：一行一条“实际生效/失败的办法”，例：
  - `europepmc REST /fullTextPDF 404；网页 ?pdf=render 429 限流`
  - `ua=浏览器+Referer 可绕过 403`
  - `profile 持久+登录一次，之后 cookies 直接下载`
- `notes`：一两句定性（这是什么、为什么有用/没用）。

## 查看类似网站（memory_query）
- `query domain=X similar=true`：返回同域/别名优先、再按共享 tags+类型排序的相似记录（找**同一服务的其它镜像**、或同类服务）。
- 可按 `resource_types`/`tags`/`verdict`/`text` 过滤。空结果 = 未知，走 `web_search` 找镜像/同类，找到后马上 `memory_remember` 记下来。

## 注意
- 记忆是**运行态**（存于插件配置的 `memory/` 目录，不入库不提交）；换 workspace 是全新记忆，不跨项目泄露。
- 记录要短、可复核：一条 methods/notes 应能让下次照做；不要堆情绪化评价。
