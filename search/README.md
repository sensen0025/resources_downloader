# Resource Hub — 资源检索(`search/`)

**多搜索引擎聚合 + 可用性筛选**:一条查询,并发打多个搜索引擎,去重合并、
相关性打分、HTTP 探测候选 URL 的可用性,返回"哪些能用、是什么类型"。

```
查询 ──► 并发引擎(bing/baidu/mojeek/360/duckduckgo/github)
        → 规范化去重(去跟踪参数,保留网盘提取码)
        → 相关性打分(标题 2.0 / URL 1.5 / 摘要 1.0 + 网盘/直链/资源站加分)
        → (可选)可用性探测: HEAD/Range → 分类 直链📄 / 网页🌐 / 网盘☁️ / 失效 / 反爬
        → 探测重排(直链+3, 网盘+2.5, 失效-8, 反爬-5)
```

## 用法

```bash
# 全部引擎,不打分探测
python -m search.cli "python requests 教程 pdf"

# 指定引擎 + 可用性探测(推荐:筛出直链和网盘)
python -m search.cli "某书 epub" --engines bing,mojeek,duckduckgo --probe --limit 15

# JSON 输出(给脚本/Agent 消费)
python -m search.cli "设计模式 pdf" --json
```

Python API:

```python
from search import search

results = search("requests 中文文档 pdf", probe=True, limit=10)
for r in results:
    print(r.score, r.title, r.url, r.probe.kind if r.probe else "")
```

## 引擎(2026-08 本机实测可达性)

| 引擎 | 说明 | 实测 |
|---|---|---|
| `bing` | HTML 解析,跳转链接 base64 解真实 URL | ✅ |
| `baidu` | 中文首选;新 coso UI 的 `mu=` 属性直出真实 URL(免解跳转) | ⚠️ 偶发安全验证页 |
| `mojeek` | 对爬虫最友好,零跳转直链 | ✅ |
| `so360` | 中文补充;`so.com/link` 跳转有有效期(过期即失效,探测会标 unreachable) | ✅ |
| `duckduckgo` | Lite 端点(POST);**常直接返回 PDF 直链**(archive.org/readthedocs 等) | ✅ |
| `github` | API 检索仓库(软件/电子书源码);无 token 10 次/分钟,配 `GITHUB_TOKEN` 更高 | ✅ |

## 关键设计(对齐前人经验)

- **引擎注册表 + 熔断**(yt-dlp/crawlee):连续失败 3 次冷却 5 分钟,单个引擎被拦不影响整体;
- **三层去重**(crawlee):规范化 URL 为 key,跨引擎合并(记录命中引擎数),跟踪参数剔除、网盘 `pwd` 保留;
- **薄版 GenericFetcher**(yt-dlp 教训):探测只做 状态码 + Content-Type + 扩展名 分类,不堆站点特例;
- **http/https 统一为 https**:反爬时代 http 站点普遍 301,统一后可跨引擎合并;
- **网盘域名零网络快速通道**:pan.baidu.com 等直接分类,不浪费请求。

## 测试

```bash
python -m unittest tests.test_search_normalize tests.test_search_engines tests.test_search_filter -v
```

- 解析器测试用 `search/_samples/` 里的真实抓取 fixture(离线,0.03s);
- 覆盖:URL 规范化/去重/网盘识别、五引擎解析、相关性打分、探测分类。

## 已知边界(诚实标注)

- 搜索引擎返回的**时效性跳转链接**(so.com/link、部分 bing 链接)会过期,探测会标记
  unreachable —— 这不是探测 bug,是这类链接本身的属性;
- 百度偶发安全验证页,引擎会明确报错并进入熔断,而不是静默返回空;
- 相关性打分是启发式(词频 + 类型加分),不含语义;后续可接 LLM 重排(计划阶段 C)。
