---
name: site-open-access-papers
description: 学术 OA 论文获取链路：Crossref 定位 DOI → 出版商(如 MDPI)直连 → 被拒就走 Unpaywall/Europe PMC 合法开放镜像拿 PDF。实测：MDPI 直连在本环境被 Akamai 按 IP 拒，链路其余部分可用。
whenToUse: 用户要某篇学术论文/期刊文章的 PDF/全文；给了 DOI/标题/作者；目标在 MDPI 或其它开放获取期刊。
---

# open-access-papers（OA 论文链路）

## 定位
把"一篇论文"变成能落地的 PDF：**DOI 是锚点**，优先合法 OA 镜像，不硬撞出版商反爬。

## 资源与格式
PDF 全文（多数 OA 期刊）、补充材料、PMC/Europe PMC 托管版。DOI 前缀 `10.3390/`=MDPI。

## 访问路径（从稳到险）
1. Crossref 找 DOI（标题/作者检索）→ `api.crossref.org/works?query.bibliographic=..&filter=prefix:10.3390,type:journal-article&select=DOI,title,URL`（✅ 2026-09-06）
2. 出版商直连：`doi.org/<doi>` → 期刊落地页；PDF=`落地页+/pdf`
   - ✅ 2026-09-06 数据中心出口：MDPI 落地页与 /pdf 均被 Akamai `Access Denied`（换 UA 与真浏览器同拒）→ **本出口直连不可行**
3. Unpaywall 找 OA 镜像：`api.unpaywall.org/v2/<doi>?email=<真实格式邮箱>`（✅；`example.com` 假邮箱返回 422，换真实格式邮箱即通）
4. Europe PMC：`ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:"<doi>"&format=json&resultType=core` → pmcid → 全文
   - 网页 `europepmc.org/articles/<PMC>?pdf=render` ✅ 可达但 `api/getPdf` 在本出口持续 429（2026-09-06）；`webservices/rest/<PMC>/fullTextPDF` 部分文章 404（未开放全文）
5. 兜底：请用户提供其网络可访问的 PDF 直链/网盘（出版商 CDN 通常放行住宅 IP）

## 工具速查
```python
# Crossref 找 MDPI 论文(带 prefix 过滤，避免书章占位)——run_code python
GET https://api.crossref.org/works?filter=prefix:10.3390,type:journal-article&query.bibliographic=<词>&rows=5&select=DOI,title,URL
# Unpaywall 找 OA 托管(pdf)——注意 email 必须真实格式
GET https://api.unpaywall.org/v2/<doi>?email=<you@gmail.com>
# Europe PMC 查收录
GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:"<doi>"&format=json&resultType=core
```
下载一律走 `download_file`；出版商/镜像都拒时按 #5 升级，别反复撞。

## 验证与交付
`probe_file` 必须见 `%PDF` 头；交付给出 `doi + 来源(出版商/PMC) + 大小 + sha256`。

## 合规
OA 论文本身可自由下载；遵守出版商与 Europe PMC 的速率限制（429 即停）。不要绕付费墙（非 OA 文章）。

## 记忆建议
tags：`open-access` `publisher` `akamai` `ip-blocked` `mirror` `ratelimit` `doi`；methods 记录哪一跳 403/404/429、哪个镜像有效（示例见 site-directory 下卡状态与 memory 实测）。
