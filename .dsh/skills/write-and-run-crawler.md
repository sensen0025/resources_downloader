---
name: write-and-run-crawler
description: 现场写一次性爬虫。任何站点没有现成下载器时，由 LLM 自己写一段 Python/Bash 脚本（探测→抓取→解析→取出真实文件/流地址），立刻运行，按报错迭代修正；多次失败后仍不放弃，换接口/换法再试，直到拿到资源或把障碍说清楚。
whenToUse: 需要登录、反爬、页面结构复杂、资源藏在接口里、或任何「没有专门下载器」的站点；或 resource-download 侦察后发现页面里没有现成直链。
---

# write-and-run-crawler（现场写爬虫，绝不靠维护站点代码）

原则：**脚本是一次性的、为当前这一单写的**。站点改版不影响我们，因为我们每次按当时页面现写现调。不写「通用框架」、不沉淀站点专用模块——沉淀的是下面这套写与调的套路。

## 1. 动手前 30 秒侦察（写代码前先看货）
- `http_fetch`/`web_fetch` 拿页面 HTML 与响应头。记下：状态码、跳转链、是否有反爬标记（`cf-challenge`、`captcha`、`403`、`__cf_`、`验证`）。
- 找「数据真实所在」：`<script>` 里的 `window.__DATA__`/`__INITIAL_STATE__`、`<meta>`、JSON-LD、`.m3u8`、`.mp4`、接口路径（`/api/`、`/ajax/`、`graphql`）。
- 有反爬：先想清楚它挡的是「浏览器指纹」还是「Cookie/Token」。前者可尝试补 `User-Agent/Referer/Origin` 头；后者需要用户提供 Cookie——**需要凭证就向用户要，不要硬耗**。

## 2. 脚本模板（写进 scratch 再运行，别裸贴长命令）
用 `run_code` 跑。Python 优先（stdlib `urllib` 就能干活；若环境有 `requests` 更省事）：
```python
import urllib.request, json, re, sys
def get(url, **headers):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" , **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()
# 1) 先探：打印状态码 + 前 500 字符 HTML，确认没有反爬/跳转
# 2) 再解析：正则/JSON 提取 真实文件或流地址，逐个打印出来
# 3) 验证：对提取出的 URL 再发一次 HEAD/GET 看 Content-Length 与 Content-Type
```
写完 → `run_code` 跑 → 看 stdout。**每次只改一处，输出驱动迭代**：打印状态码、打印抓到的片段、打印正则命中的结果。别一次写两百行然后瞎猜。

## 3. 迭代/重试纪律（多次失败尝试的正确姿势）
- 失败 = 有输出可看。404/超时/JSON 空/正则没命中 → 根据输出改，再跑。一轮内最多 ~5 次小迭代。
- 连续 2 次相同失败 = 策略错了，换路子：接口 → 页面 → 换个搜索词找同资源镜像 → 换下载源。
- 3 个不同策略都失败：向用户汇报证据与选项（要 Cookie？换格式？换站点？放弃？），等指示，不无限自转。
- 耗时长（大批量/大文件）优先 `resource-download` 的批量路径：抓「清单」→ 循环下载每一项，带断点续传。

## 4. 反爬常见解法（按成本从低到高）
1. 补头：`User-Agent` / `Referer` / `Origin` / `Accept-Language`（90% 的 403 是被默认 UA 拒）。
2. 换面：改走其**公开接口**（常无 UA 校验）或移动端子域/API。
3. 带凭证：把用户提供的 Cookie 原样放 `Cookie:` 头里重试。
4. 放缓：加 `sleep`、降并发，绕过简单频控。
5. 上浏览器：纯 HTTP 拿不到时（JS 渲染、Cloudflare“Just a moment”、需要点击才出文件），
   用 `browser` 工具 open（带 wait_ms 等挑战自解）→ act 点击/填表 → 需要时 `cookies`
   导出 Cookie 头回灌 `http_fetch`/`download_file`，或 `browser` 的 download 直接抓点击文件。
   **撞上验证码 → 交给 captcha-handling 技能**（分类→通用识别→人工/凭证兜底）。
   profile 持久：登录一次，后续步骤自动带登录态。
6. 以上都不行 → 把「站点挡我们什么、页面证据」发给用户，请其提供 直链/网盘链接/登录态，而不是僵住。

## 5. 写完即清
脚本放 scratch 目录（仓库外或已被 gitignore 的 `.rd_scratch/`），跑完不用清理可复用；**不要把站点专用脚本提交进仓库**（它们会过期、需要维护——这正是本仓库要避免的）。
