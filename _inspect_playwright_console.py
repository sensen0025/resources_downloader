import sys
from playwright.sync_api import sync_playwright
from proxy import playwright_proxy

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def run():
    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled"]
        }
        proxy = playwright_proxy()
        if proxy:
            launch_args["proxy"] = proxy
            print("Using proxy:", proxy)
        
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        
        page = context.new_page()
        
        # Listen to console messages and page errors
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))
        
        print("Navigating to play page...")
        page.goto("https://agedm.io/play/20200283/8/10", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        
        print("Clicking player...")
        try:
            iframe = page.frame(url=lambda u: "wuzhoupai" in u)
            if iframe:
                iframe.click(".art-video")
                print("Clicked!")
        except Exception as e:
            print("Click error:", e)
            
        page.wait_for_timeout(6000)
        browser.close()

if __name__ == "__main__":
    run()
