---
name: biquge-novel-crawler
description: 笔趣阁小说全本/章节抓取并合成单体 TXT(书号或书页 URL 输入)
whenToUse: 用户要下载笔趣阁类小说整本或某段章节的 TXT(如「万相之王 全本 txt」「凡人修仙传 前 10 章」)
---

# biquge-novel-crawler

调用 Resource Hub 的 `skill_invoke` 工具(name=`biquge_novel_crawler`)执行。
**不要自己写脚本逐章抓笔趣阁页面** —— 本技能已实现 目录解析→并发拉取→广告/水印行过滤→单体 TXT 合成。

## 参数(经 skill_invoke.args 传入)

- `book_url_or_id`(必填):书页 URL(如 `https://www.biquges123.com/50045/`)或纯书号(如 `50045`)
- `max_chapters`(可选):抓取章节上限;`0` 或省略 = 全本。试读/快速验证给 5~20
- `export_txt`(可选,默认 true):合成 `<书名>.txt`
- `output_dir`(可选,默认 downloads/novels,Resource Hub 服务端路径)

## 返回

- ok=true + 文件路径/章节数:成功(export_txt 时 message 含 path)
- ok=false + 原因:书页抓不到(站换域名)、无章节目录、有效章节 <3(反爬)

## 边界与建议

- 全本(千章级)耗时数分钟且对站点有压力 —— 告知用户等待;或被 CF 拦时改用 `resource_fetch(query, seed_urls=[书页URL])` 走任务管线+浏览器 Agent。
- 同一批多次全本抓取会被站点限流 —— 失败后间隔重试或缩小范围。
- 与任务管线小说路径的关系:resource_fetch(query 含「小说/全本」)也会命中管线内置 novel 技能;本技能是定向增强(并发+更强广告过滤+书号直入)。
