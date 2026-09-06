---
name: site-gdgame
description: GDGAME(gdgame.org) 单机游戏资源页：页面"游戏下载"区给多个网盘(夸克/百度/UC/123/移动…)二维码，扫码=手机转存后下载。二维码是页面用 jquery.qrcode 由明文分享链接现场生成 → 两条路：直接提取分享链接(快) 或 浏览器截图各 .qr-box 出真 PNG。
whenToUse: 用户要 gdgame.org 上某单机游戏资源、问"这游戏的网盘/二维码/提取码/解压密码"，或给 /n-1/<id>.html 详情页链接时。
---

# GDGAME（单机游戏 · 多网盘二维码资源页）

## 定位
gdgame.org：单机游戏分享站。详情页"游戏下载"区（`.download-section`）并列 **多个网盘卡片**：
夸克/百度(推荐、常带提取码)、UC/123/移动(旧资源)、迅雷(偶有直链分享 `pan.xunlei.com/s/…?pwd=`)、
联通等——页面提示"**扫描二维码下载**：手机网盘转存后，电脑网盘再下载"。
✅ 2026-09-06（海外数据中心出口）可达：详情页 HTTP 200、无 CF。

## 资源与格式
游戏本体压缩包（网盘内）；页面本身只给"分享入口(二维码/链接)+提取码+可能含解压密码/说明"，
**不直接提供文件直链** → 交付形态 = 各网盘 分享链接 + 提取码 + 二维码 PNG（供用户扫码转存）。

## 关键机制（先理解再动手）
"二维码"不是图片文件：页面用 `jquery.qrcode.min.js` **运行时把明文分享链接渲染成二维码**
（例：`$('#qrcode1').qrcode({ text: "https://pan.baidu.com/s/…?pwd=1234", … })`）。
→ 每个网盘的真实链接就在页面 `<script>` 明文里，两条获取路任选。

## 访问路径
1. 详情页 URL 形态：`https://gdgame.org/n-1/<id>.html`；站内搜索 `https://gdgame.org/?s=<关键词>`。
2. **路 A（快，纯 HTTP）**：抓页面源码，用正则提取 `$('#qrcode<编号>').qrcode({ text: "<链接>"`；
   反向定位卡片名（该 `qrcodeN` 之前最近的 `.download-card-title` 文本）得到
   网盘→链接映射（✅ 2026-09-06 实测：qrcode1=百度、qrcode2=夸克、qrcode4=UC、qrcode5=123、qrcode7=移动）。
   百度盘链接 `?pwd=` 即提取码；页面另有 "提取码：XXXX" 文本可兜底核对。
3. **路 B（要真 PNG 时）**：`browser` 开详情页 → `eval` 把 `.download-section` scrollIntoView →
   wait（二维码按需渲染）→ 逐个 `shot` `#qrcode1/2/4…` 元素存 PNG（✅ 2026-09-06 实测三张，
   PNG 魔数正确，可交付给用户扫码）。若只想本地生成同内容 PNG：提取路 A 链接后可用可选
   `qrcode` python 库重绘（🧪 未实测）。
4. 解压密码/说明：页内搜 `解压密码`；该站 help 页(站点右上"不会下载/解压密码是什么")为通用说明。

## 已知坑
- 二维码是 canvas/JS 生成：**直接爬 `<img>` 或 OCR 页面拿不到**，必须走路 A 或浏览器截图。
- 卡片可能分"主更新资源/旧资源"、移动盘可能带倒计时解锁（PC 端 `#yidong-card-primary`）→ 以实际卡片为准。
- 分享链接有效性与提取码以页面为准；夸克/百度需用户账号转存，**扫码是用户人工一步**（无 web 终端时交付 PNG+链接+密码即可）。

## 验证与交付
路 B 产物用 `probe_file`（PNG `89504e47`）。交付：
```
{ game: "标题", pan_links: [{pan:"夸克", share:"…", qr_png:"downloads/gdgame/…/qr_quark.png"}, …],
  extract_code: "1234", unzip_hint: "…" }
```

## 合规
单机游戏资源版权灰色：仅个人学习/试用，用户自担；不做推广、不二次分发。

## 记忆建议
tags：`game` `pan` `qrcode` `gdgame`；methods 记录"二维码=明文分享链接渲染"机制与卡片映射示例；域名失效/结构改版就更新本卡日期。
