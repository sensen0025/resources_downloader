# Resource Hub — 页面分析(`pages/`)

**找源核心**:对候选 URL 做 抓取 → 元数据/资源链接提取 → 分级,决定走哪条获取路径。

```
URL → fetch(带缓存/可选 TLS 模仿) → extract(OG/JSON-LD/直链/网盘/下载按钮/iframe)
    → classify: direct_file / download_page / pan_share / login_required /
                aggregator / blocked / dead / unknown(可 LLM 复核)
```

## 用法

```bash
python -m pages.cli "https://www.minecraft-schematics.com/schematic/31398/" --probe
python -m pages.cli <url> --llm --json
```

```python
from pages import analyze_page

a = analyze_page(url, probe=True)
print(a.page_class.value, a.reason)
for r in a.best_resources:
    print(r.kind, r.url)
```

## 关键设计(对齐前人经验)

- **薄版 GenericFetcher**(yt-dlp 教训):只认标准模式(OG/JSON-LD/扩展名/下载按钮/iframe),
  不堆站点特例;不确定的页面交给 classify 的 LLM 复核(`--llm`);
- **分级驱动路径选择**:直链→直接下载;下载按钮详情页→浏览器 Agent(点击+登录);
  网盘→网盘解析(后续);反爬→Agent 等 Cloudflare;失效/拦截→跳过;
- **相对路径解析 + 去跟踪参数 + URL 去重**;`file_ext` 支持 `.litematic/.schematic` 等 12 字符扩展名;
- **探针联动**:复用 search.probe 的直链/反爬/失效分类,避免重复请求。

## 实测(2026-08,真实站点)

- minecraft-schematics.com 详情页 → `download_page`,提取出 `/schematic/{id}/download/`;
- 下载端点落地页标题 "Login" → 分级器正确判为需登录(minecraft-schematics.com / mineschematic.com 实测都需登录);
- planetminecraft.com / curseforge.com → `blocked`(Cloudflare),走 Agent 路径。

## 测试

```bash
python -m unittest tests.test_pages -v
```
