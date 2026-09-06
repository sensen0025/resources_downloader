---
name: captcha-handling
description: 人机验证（验证码）处理。把验证码分类（字符图/滑块拼图/极验 Geetest/Cloudflare Turnstile/hCaptcha/reCAPTCHA/行为验证），按“通用本地识别 → 人工/凭证兜底”的顺序处理，并把每类挑战的处理方法与结果写入记忆。识别依赖为可选项（ddddocr/GeekedTest），装了才自动尝试，没装明确报错降级。
whenToUse: 遇到验证码/人机验证/JS 挑战/滑块/点选/登录前校验（403 页面、登录表单、Cloudflare “Just a moment”、Geetest、hCaptcha、reCAPTCHA、Turnstile 等）时。
---

# captcha-handling（人机验证处理）

先分类，再选择手段；**任何自动尝试最多 2 次**，失败立刻升级，别把时间耗在破解上。

## 1. 分类（用 browser/text 判断是哪类）
| 挑战特征 | 类型 | 手段 |
|---|---|---|
| 图片上有字符/数字，旁边有输入框 | 字符验证码 | `solve kind=ocr`（装 ddddocr 后自动识别回填） |
| 背景凹槽 + 可拖滑块 + 小拼图块 | 滑块拼图 | `solve kind=slider`（ddddocr 缺口检测 + 自动拖动） |
| 页面出现 `gt4`/geetest 弹窗，滑块/图标点选/围棋 | 极验 Geetest v4 | `solve kind=geetest`（可选 GeekedTest，需从页面取 `captcha_id`+`risk_type`） |
| 页内 iframe/challenge 刷转、标题 “Just a moment”/`cf-chl` | Cloudflare / Turnstile | 不自动破：等自解（open+wait）→ 不行转人工/凭证 |
| reCAPTCHA/hCaptcha/行为类 | 商业行为验证 | 不自动破：转人工（用户解一次/提供 Cookie）或付费聚合（若部署配置了 key） |

## 2. 标准动作序列（用 `browser`，同一持久会话内完成）
1. `open`（带 wait_ms，等挑战出现）→ `text`/截图判断类型与控件 selector。
2. 字符 → `solve {kind:"ocr", img_selector, input_selector}`；滑块 → `solve {kind:"slider", bg_selector, target_selector, handle_selector}`。
3. Geetest：先用 `eval` 从页面配置取 `captcha_id`（window/init 脚本/请求参数）与 `risk_type`，再 `solve {kind:"geetest", captcha_id, risk_type}`；返回 tokens 后按页面回调需要注入/提交。
4. 提交表单/继续 → `text` 验证是否真过了（出现了目标内容/不再报错）——**没验证通过不算成功**。
5. `memory_remember`：域 + verdict + tags（`antibot`、`turnstile`/`cf`/`geetest`/`char`/`slider`…）+ methods（哪个 selector、哪种解法有效）。

## 3. 依赖（全部可选；缺失时工具给安装提示而不是崩）
```bash
pip install ddddocr            # 字符 OCR + 滑块缺口（会带 opencv/numpy/onnxruntime，体积较大）
pip install git+https://github.com/xKiian/GeekedTest.git   # 极验 v4 slide/icon/gobang/ai
```
未安装时：`solve` 返回明确错误 → 按第 4 节升级，不要反复重试。

## 4. 升级与边界（诚实优先）
- 自动 2 次失败 / 依赖缺失 / 属于 Turnstile 或行为类 → **把截图与证据交给用户**：请用户提供 一次性验证码 / Cookie / 登录态 / 直链，或说明由用户本人在浏览器里完成一步。
- 任何站点都可能检测“破解”行为并封号/封 IP：**遵守目标站 ToS**；给资源下载而非批量注册等滥用场景用。
- 部分站点的验证码需要其专属逆向（符号运算/风控参数），GeekedTest 等第三方库可能随时失效——失效就记录进记忆并转人工，不要执着。
