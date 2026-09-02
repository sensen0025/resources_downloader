---
name: haowallpaper-4k-extractor
description: 哲风壁纸(haowallpaper.com)详情页→CDN 图片直链提取与下载(输入页面 URL 或图片 ID)
whenToUse: 用户要哲风壁纸某张壁纸的原图/高清图(如「haowallpaper 韩立壁纸」「这个壁纸链接的大图」)
---

# haowallpaper-4k-extractor

调用 Resource Hub 的 `skill_invoke` 工具(name=`haowallpaper_4k_extractor`)执行。
**不要自己逆向哲风壁纸的 Nuxt SPA** —— 本技能已实现 详情页→底层 CDN 直链(getCroppingImg/previewFileImg)解析与下载。

## 参数(经 skill_invoke.args 传入)

- `url_or_id`(必填):详情页 URL(如 `https://haowallpaper.com/homeViewLook/19580937584989056`)或图片 ID(如 `19580937584989056`)
- `download`(可选,默认 true):下载到 Resource Hub 服务端 downloads/wallpapers(魔数校验 + 尺寸读取)
- `output_dir`(可选)

## 返回

- ok=true:file_id/title + image_url(CDN 直链)+ path/size/width/height(下载时)
- ok=false:页面抓不到/CDN 无直链(结构变更)

## 边界与建议

- 站内「4K 原图下载」通常需登录或积分 —— 技能交付**页面公开可达的最大图**(preview 与 cropping 取大),
  若宽 <1920 会在 data.note 里说明,不要承诺 4K。
- CDN 直链可直接交付用户下载(无 Referer 限制,已实测 200)。
- 详情页 ID 与 CDN 文件 ID 不同(页面 homeViewLook/19580937584989056 → CDN 19580921404412800) —— 以技能返回的 image_url 为准。
