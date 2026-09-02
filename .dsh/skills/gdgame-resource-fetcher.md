---
name: gdgame-resource-fetcher
description: gdgame.org 单机游戏详情提取(百度/夸克/UC 网盘直链+提取码/解压密码)与全库搜索
whenToUse: 用户要 gdgame.org 上的单机游戏资源(如「gdgame 风暴崛起 网盘」「这个游戏的解压密码」);或用关键词在 gdgame 找游戏
---

# gdgame-resource-fetcher

调用 Resource Hub 的 `skill_invoke` 工具(name=`gdgame_resource_fetcher`)执行。

## 参数(经 skill_invoke.args 传入)

- `target`(必填):游戏详情 URL(如 `https://gdgame.org/n-1/1159.html`)或纯 ID(如 `1159`)或搜索关键词(如 `风暴崛起`)
- `action`(可选,默认 get_game_detail):`get_game_detail` 提取详情+网盘+密码;`search_games` 全库搜索

## 返回

- get_game_detail:ok=true + title + `pan_links[]`(provider: baidu/quark/uc + url)+ `extract_code`(提取码/解压密码)
- search_games:ok=true + results[](title+url,最多 20)

## 边界与建议

- 详情页的网盘链接通常需复制后由用户自行转存;提取码/解压密码与用户描述一致时直接交付。
- 站内搜索(?s=)按标题匹配且可能返回最新游戏列表 —— 若结果与关键词明显无关,提醒用户用详情 URL 直查。
- 网盘转存/解析走 Resource Hub 夸克链路(quark cookie)或 pan_links 交付,本技能不下载游戏本体。
- 单机游戏资源版权灰色 —— 按用户自己的用途交付,不做推广性描述。
