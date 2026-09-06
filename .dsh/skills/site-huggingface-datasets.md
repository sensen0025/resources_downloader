---
name: site-huggingface-datasets
description: Hugging Face 数据集获取：公开数据集可匿名取(文件/结构化 rows)；gated(半封闭)数据集须登录+授权(401)，交给用户提供 HF token/登录；大数据集用流式/分片与 rows 取样，不整包硬拉。
whenToUse: 用户要下载/读取某个 Hugging Face 数据集；或说"HF 上某数据"；或遇到 gated 数据集提示未授权时。
---

# Hugging Face 数据集

## 定位
HF 数据集两大类：**公开**(匿名可读可下) 与 **gated/半封闭**（公开可见、需登录+同意条款，HTTP 401）。

## 资源与格式
parquet / jsonl / csv / image/audio 分片；元数据 README；自动转换 parquet；datasets-server 结构化取行。

## 访问路径
1. 先查库：`https://huggingface.co/api/datasets?search=<关键词>`（✅ 2026-09-06 匿名可用）
2. 看 gated：返回里 `gated` 字段（`false`=公开；`manual`/`true`=半封闭）
3. 文件树：`api/datasets/<id>/tree/main[/子目录]`（gated 也可见树与大小，✅）
4. 公开数据集取数（按体积从小到大）：
   - 小文件/样例：`datasets/<id>/resolve/main/<path>`（gated 时此处 401，✅ 实测 sander-wood/m4-rag）
   - 结构化取样（推荐，避免拉整包）：`https://datasets-server.huggingface.co/splits?dataset=<id>` → config/split → `rows?dataset=<id>&config=<cfg>&split=<split>&offset=0&length=N`（✅ 2026-09-06 匿名可取行）
   - parquet 分片：`api/datasets/<id>/tree/main/<cfg>` 看 shards，用 `download_file` 只拉需要的分片（实测单分片 ~300MB）
5. gated：如实告诉用户"需 HF 账号在页面同意条款 → 提供 HF token(可选 `HF_TOKEN`)或让用户授权"，拿到 token 后经 `run_code` 设 `HF_TOKEN` 或 `browser` 登录一次导出 Cookie 再取；不猜 token。

## 工具速查
```bash
curl "https://datasets-server.huggingface.co/rows?dataset=<org%2Fname>&config=<cfg>&split=<split>&offset=0&length=5"
```
大语料不要 `download_file` 整包 16GB；用 rows 取样/只拉所需分片，并向用户说明量级与取舍。

## 验证与交付
`probe_file`(小文件) 或 rows 返回的 `features/rows` 结构；交付给出 `dataset id + config/split + 样例行数 + 是否 gated`。

## 合规
遵守数据集许可（多为 CC 系/研究用途，如 cc-by-nc-nd）；gated 条款必须由账号持有人同意。

## 记忆建议
tags：`dataset` `gated` `hf` `parquet`；大文件场景记 `methods:"用 datasets-server rows 取样 / 只拉分片"`。
