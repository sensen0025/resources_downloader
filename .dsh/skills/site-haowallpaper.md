---
name: site-haowallpaper
description: 哲风壁纸(haowallpaper.com)高清壁纸获取：详情页(Nuxt SPA)里埋着 CDN 直链字段(getCroppingImg/previewFileImg 等)，取最大公开图直链下载。站内"4K 原图"常需登录/积分，别承诺 4K。
whenToUse: 用户要哲风壁纸某张壁纸的大图/原图(给详情页 URL 或图片 ID，如 haowallpaper.com/homeViewLook/<id>)时。
---

# 哲风壁纸（haowallpaper）

## 定位
哲风壁纸是 Nuxt SPA：**别硬爬渲染后的页面**，详情页 HTML/接口里带底层 CDN 直链字段。
给"页面公开可达的最大图"，站内 4K 下载按钮常需登录/积分 → 先看清页面到底放不放行。

## 资源与格式
壁纸大图（jpg/webp），CDN 直链；页面 ID ≠ CDN 文件 ID（以抓到的 image_url 为准）。

## 访问路径
1. 详情页形态：`https://haowallpaper.com/homeViewLook/<图片ID>`（如 …/19580937584989056）。🧪 2026-09-06 未实测（沿用旧卡知识，结构以实测为准）
2. 解析：抓详情页/其数据接口，找 `getCroppingImg`/`previewFileImg` 等 CDN 直链字段，取最大一张。
   → 这些字段随 SPA 结构变化，写一次性爬虫时先打印命中的 URL 集合再选大图（write-and-run-crawler）。
3. 下载：`download_file` 直链（旧卡记录无 Referer 限制、可 200；🧪 待实测复核）。

## 已知坑
- 登录/积分墙挡住"4K 原图"：能交付的是页面公开可达的最大图；宽 <1920 时如实注明，别承诺 4K。
- SPA 结构易改版：失败就现场按新结构调，并把新字段写 memory（不要沉淀站点代码）。

## 验证与交付
`probe_file`：jpg `ffd8ff`/webp `RIFF`、非空；交付 原页面 URL + image_url + path/大小/宽高（若能取到）。

## 合规
壁纸版权归作者/站点：按用户指定单张获取，不批量拉库、不绕付费。

## 记忆建议
tags：`image` `wallpaper` `spa` `cdn`；methods 记录可用 CDN 字段名与改版日期。
