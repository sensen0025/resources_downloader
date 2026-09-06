---
name: site-annas-archive
description: Anna's Archive (https://annas-archive.gd/) 电子书/文献检索与下载知识卡：聚合 Z-Library/LibGen/Sci-Hub。支持 DDoS-Guard 浏览器自动过盾、多轮检索、慢速合作节点直链提取与多镜像轮询下载（EPUB/PDF/MOBI）。✅ 2026-09-06 本机实测完整闭环。
whenToUse: 用户要找电子书/期刊论文/文献，并指定 Anna's Archive（如 annas-archive.gd / annas-archive.org）或镜像站时。
---

# Anna's Archive（https://annas-archive.gd/）

## 一句话定位
全球最大的影子图书馆元聚合搜索引擎（索引 Z-Library、LibGen、Sci-Hub、Internet Archive 等电子书与文献）。

## 资源与格式
- 电子书：EPUB (`504b0304`)、PDF (`25504446`)、MOBI/AZW3、DJVU
- 元数据：包含 MD5、作者、出版年份、语言、文件大小、ISBN 等

## 访问路径（从稳到险排序）
1. **可用主域**：`https://annas-archive.gd/`（备用镜像：`annas-archive.gl`, `annas-archive.pk`；历史域名 `.org`, `.se`, `.li` 多已被封或停放）
2. **过盾（DDoS-Guard）**：
   - 站点开启 DDoS-Guard JS 挑战。必须使用 Playwright 或带持久 Cookie 的无头浏览器，访问首页等待 3~8 秒自动放行。
3. **检索**：
   - 保持同一浏览器 context，提交搜索 `input[name="q"]` 或请求 `/search?q=<关键词>`。
   - 提取结果列表中的 MD5 标识符：`/md5/<32位MD5>`。
4. **获取下载链接**：
   - 进入 `/md5/<md5>` 详情页，免费下载渠道为 **Slow Partner Server** 列表（`/slow_download/<md5>/0/0` 到 `/slow_download/<md5>/0/7`）。
   - 访问 `/slow_download/...` 落地页（等 DDoS-Guard 放行），从页面中提取 `📚 Download now` 或合作服务器直链（如 `https://*.net/d4/...`、`http://45.3.*:6060/d4/...`）。
5. **高速下载**：
   - 将直链交给 `curl -L` 或 `download_file`，携带 `Referer: https://annas-archive.gd/` 即可直接拉取完整文件，无需排队验证码。若某节点 500/502/断流，自动轮询下一个 partner 节点。

## 已知反爬/风控
- **DDoS-Guard JS Challenge**：
  - 现象：HTTP 200 返回包含 `DDoS-Guard` 挑战脚本，curl 直接访问无法获取真实 HTML。
  - 实测结论（✅ 2026-09-06）：Playwright 无头 Chromium 启动时关闭 `AutomationControlled`，加载页面等待 3~8s 即可 100% 自动过盾。
- **慢速节点波动**：
  - 部分 partner 节点在海外出口偶尔出现 502/500 或 TLS 断流；对策：并发或依次轮询 0~7 号 partner，命中即下载。

## 工具速查（Python 自动化下载模板）

```python
from playwright.sync_api import sync_playwright
import time, re, subprocess, os, hashlib, zipfile

def download_book(keyword, output_dir="/home/sensen/Downloads/books"):
    os.makedirs(output_dir, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        page = ctx.new_page()

        # 1. 过首页盾
        page.goto("https://annas-archive.gd/", timeout=30000)
        page.wait_for_selector('input[name="q"]', timeout=20000)

        # 2. 检索
        with page.expect_navigation():
            page.fill('input[name="q"]', keyword)
            page.keyboard.press("Enter")
        time.sleep(5)

        # 3. 提取 MD5 列表
        links = page.query_selector_all('a[href*="/md5/"]')
        md5_list = [l.get_attribute('href').split('/md5/')[-1].strip('/') for l in links if '/md5/' in (l.get_attribute('href') or '')]

        # 4. 轮询下载
        for md5 in md5_list[:5]:
            for p_idx in range(8):
                page.goto(f"https://annas-archive.gd/slow_download/{md5}/0/{p_idx}", timeout=30000)
                time.sleep(5)
                # 提取直链
                for a in page.query_selector_all('a'):
                    href = a.get_attribute('href') or ''
                    if 'anon/s/' in href or any(ext in href.lower() for ext in ['.epub', '.pdf', '.mobi']):
                        tmp_file = f"{output_dir}/temp.bin"
                        subprocess.run(['curl', '-L', '--max-time', '60', '-A', 'Mozilla/5.0', '-H', 'Referer: https://annas-archive.gd/', '-o', tmp_file, href], capture_output=True)
                        if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 1000:
                            magic = open(tmp_file, 'rb').read(4).hex()
                            ext = ".epub" if magic == "504b0304" else ".pdf" if magic.startswith("25504446") else ".bin"
                            final_name = f"{output_dir}/{keyword.replace(' ', '_')}{ext}"
                            os.rename(tmp_file, final_name)
                            browser.close()
                            return final_name
        browser.close()
    return None
```

## 验证与交付要点
- 使用 `probe_file` 或文件头魔数验证：
  - EPUB：魔数必须为 `504b0304`，且可作为 Zip 解包读取 `mimetype`。
  - PDF：魔数必须为 `25504446`（`%PDF`），且包含有效 trailer。
- 交付元数据：文件路径、大小 (Bytes/MB)、SHA-256 哈希值、来源 MD5。

## 合规
- Anna's Archive 索引大量受版权保护的书籍与学术期刊。
- 仅用于个人学术研究、学习验证及公有领域文献检索，严格遵守当地法律法规。

## 记忆建议
- tags：`ebook` `annas-archive` `shadow-library` `ddos-guard` `playwright` `pdf` `epub`
- methods：主域名使用 `https://annas-archive.gd/`；Playwright 必须等待 DDoS-Guard 校验（3~8s）；下载走 `/slow_download/<md5>/0/<0..7>` 轮询提取直链后由 curl 接管。
