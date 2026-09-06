---
name: site-littleskin
description: LittleSkin(皮肤站)角色皮肤/披风获取：官方 Yggdrasil API 按角色名→材质直链最稳；页面 skinlib 按 tid 也有 raw 直链。注意 littleskin 的 name→uuid 接口形态曾与文档不符(404)，备 Mojang 官方同名 API 链。
whenToUse: 用户要某个 Minecraft 皮肤站(littleskin 或通用)角色的皮肤/披风 PNG、或说"XX 的皮肤贴图/材质"。
---

# LittleSkin / Minecraft 皮肤

## 定位
拿"角色皮肤 PNG 直链"两条路：官方 Yggdrasil API（稳定）或 skinlib 页 raw 直链（要具体 tid）。本卡含本环境实测记录。

## 资源与格式
皮肤 PNG（64×64/128×128）、披风 PNG；skinlib 预览图非贴图本体，别下错。

## 访问路径
1. **角色名 → uuid → 材质 URL（Yggdrasil）**
   - LittleSkin：`https://littleskin.cn/api/yggdrasil/profiles/minecraft/<name>` 与批量 POST `/profiles/minecraft`
     - ✅ 2026-09-06：GET 单名路径 **404**、POST 批量亦 404（接口形态与 README 宣称不符）→ 别用
   - 会话材质：`https://littleskin.cn/api/yggdrasil/sessionserver/session/minecraft/profile/<uuid>` → properties[0].value(base64) → `textures.SKIN.url`
     - 🧪 2026-09-06 未单独实测（因上一步失败即换路）
2. **备路（实测通，2026-09-06）**：Minecraft 官方同名 API 链
   - `GET https://api.mojang.com/users/profiles/minecraft/<name>`（如 Steve → uuid `8667ba71…`）
   - `GET https://sessionserver.mojang.com/session/minecraft/profile/<uuid>` → textures SKIN url
   - `download_file` 下 PNG（`textures.minecraft.net`，HTTP 可直连）→ `probe_file` 见 `89504e47` PNG ✅
   - ⚠️ Mojang API 有速率限制（429 时退避重试）
3. skinlib 页面按 tid：`https://littleskin.cn/skinlib/show/<tid>` 的 raw/`<tid>` 直链（🧪 未实测，README 时代形态）

## 工具速查
见卡内两步 GET；写爬虫时按 write-and-run-crawler 先探测接口返回再固化。

## 验证与交付
`probe_file`：PNG 魔数 `89504e47`、非空；交付 角色名/uuid + 来源 API + 大小 + sha256。

## 合规
皮肤版权属作者；仅取用户指定/有权使用的材质，不批量抓取。

## 记忆建议
tags：`skin` `api` `image`；methods 记录"littleskin name 接口 404 → 用 Mojang 同名链"教训与 429 退避经验。
