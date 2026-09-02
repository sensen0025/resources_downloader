---
name: annas-archive-book-finder
description: Annas Archive/Libgen 图书寻源——按书名/作者/ISBN/MD5 检索 epub/pdf/mobi,返回 md5 与下载直链(多镜像轮询+Libgen 兜底)
whenToUse: 用户要找电子书(epub/pdf/mobi)的开放来源/直链(如「找《XXX》epub」「这本的 md5 直链」),或已知 md5 要构造下载链接
---

# annas-archive-book-finder

调用 Resource Hub 的 `skill_invoke` 工具(name=`annas_archive_book_finder`)执行。
**不要自己写脚本轮询 Annas/Libgen** —— 本技能已实现 多镜像轮询(annas .li/.org/.se → libgen .li/.vg/.is/.rs/.st)+ CF/指纹检测 + Libgen 旧式 index.php 解析。

## 参数(经 skill_invoke.args 传入)

- `query`(必填):书名/作者/ISBN/32 位 md5
- `preferred_format`(可选,默认 any):`any`/`epub`/`pdf`/`mobi`

## 返回

- ok=true:results[](title/ext/size/md5/detail_url/download_candidates[library.lol/main/{md5} 等])+ source
- 错误码 `CLOUDFLARE_BLOCKED`:目标镜像被 CF/指纹门拦截 —— **此时改用 `resource_fetch(query, seed_urls=[书名或来源页])` 走浏览器 Agent 过盾**,不要重试 HTTP
- ok=false:所有镜像失败(blocked[] 列出原因)

## 边界与建议

- **版权与合规**:Annas Archive/Z-Library 是影子图书馆,检索结果多为受版权保护图书。按用户自己的用途交付;不主动扩散。
- 网络环境:镜像按地区封锁差异大(local 常只有 .li/.vg 可达);数据中心 IP(服务器)常被 CF 挡 → CLOUDFLARE_BLOCKED 是常态路径,不是异常。
- md5 直链(library.lol/main/{md5})可能需真实浏览器/特定镜像;detail_url(ads.php?md5=)列全镜像。
- 若要真正下载文件本体,建议 resource_fetch 走浏览器 Agent 抓 detail_url 的下载按钮。
