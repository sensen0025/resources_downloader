---
name: site-project-gutenberg
description: Project Gutenberg 公版书：标题页/EPUB/纯文本直链，无登录无验证，稳定可下。适合"某某公版书/名著全文"。直链形态固定：/cache/epub/<id> 与 /ebooks/<id>。
whenToUse: 用户要公版/公共领域书籍全文(纯文本 txt/epub/kepub)，如《傲慢与偏见》等名著或 1929 年前出版的老书。
---

# Project Gutenberg（公版书）

## 定位
公版书全文库（可免费合法下载的 TXT/EPUB/HTML）。**无登录、无验证码**，是电子书类里最稳的源。

## 资源与格式
EPUB（kepub 变体）、纯文本 UTF-8、HTML；另有有声书(音频)。

## 访问路径
1. 找 ID：官网搜索 `https://www.gutenberg.org/ebooks/search/?query=<书名>`；或 `web_search "<书名> gutenberg"`
2. 直链（ID 已知直接拼）：
   - 纯文本（推荐，实测 ✅ 2026-09-06）：`https://www.gutenberg.org/cache/epub/<id>/pg<id>.txt`
     （例：《傲慢与偏见》1342 → `…/pg1342.txt`，772 KB，Content-Type text/plain）
   - 最新 EPUB：`https://www.gutenberg.org/ebooks/<id>.epub.images`（🧪 未实测，形态稳定）
   - 首页/HTML：`https://www.gutenberg.org/ebooks/<id>`
3. 下载走 `download_file`（直接可下）。

## 工具速查
```
download_file(url=https://www.gutenberg.org/cache/epub/1342/pg1342.txt, out_name=pride_and_prejudice.txt)
```

## 验证与交付
`probe_file`：非空、`looksBinary:false` 文本即可；给出文件路径+大小+sha256+来源 ID。

## 合规
公共领域/公版，合法可自由下载分发（注意各版次版权差异，选 PG 官方版最稳）。

## 记忆建议
tags：`ebook` `public-domain`；methods 记 "cache/epub/<id>/pg<id>.txt" 直链形态即可复用。
