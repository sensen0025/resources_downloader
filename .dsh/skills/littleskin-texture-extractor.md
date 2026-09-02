---
name: littleskin-texture-extractor
description: LittleSkin 玩家皮肤/披风 PNG 材质直链提取(角色名或 32 位 UUID,走官方 Yggdrasil API)
whenToUse: 用户要某 LittleSkin 角色的皮肤贴图/披风 PNG 或直链(如「LittleSkin 上 Steve 的皮肤」);littleskin.cn 的 skinlib 皮肤按用户名/材质 hash 溯源
---

# littleskin-texture-extractor

调用 Resource Hub 的 `skill_invoke` 工具(name=`littleskin_texture_extractor`)执行。
**不要自己抓 littleskin 页面** —— 本技能走官方 CSL/Yggdrasil API:
profiles/minecraft(名字→uuid)→ sessionserver profile(→ textures JSON)→ SKIN/CAPE PNG 直链。

## 参数(经 skill_invoke.args 传入)

- `username_or_hash`(必填):角色名(如 `Steve`/`Alex`)或 32 位材质 UUID/hash
- `texture_type`(可选,默认 all):`skin` / `cape` / `all`
- `download`(可选,默认 false):是否把 PNG 下载到 Resource Hub 服务端 downloads/skins
- `output_dir`(可选)

## 返回

- ok=true:uuid/name + skin_url/cape_url(按 texture_type),download=true 时还有 files[](PNG 落盘)
- ok=false:角色不存在(未注册)/无任何材质/API 异常

## 边界与建议

- `Steve`/`Alex` 是 LittleSkin 官方预置角色,可直接验证链路。
- 返回的 texture URL(`littleskin.cn/textures/{hash}`)无需登录即可下载 PNG —— 直接交付给用户。
- 下载的 PNG 是 64x64/128x128 皮肤贴图本体;预览页(非 textures 域)不是贴图。
- 若用户给的是 littleskin skinlib 页面(skinlib/show/{tid}),用 extractor 的 raw/{tid} 直链路径
  (resource_fetch seed_urls)更直接 —— 本技能面向「按角色名/UUID 溯源」。
