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
        
        responses = []
        
        def handle_response(response):
            responses.append(f"Response: {response.status} {response.url}")
                
        page.on("response", handle_response)
        
        print("Navigating to play page...")
        page.goto("https://agedm.io/play/20200283/8/10", wait_until="networkidle", timeout=30000)
        
        # Click the play button or wait
        print("Waiting for player to initialize...")
        page.wait_for_timeout(10000)
        
        # Take a screenshot to visualize player state
        page.screenshot(path="downloads/player_screenshot.png")
        print("Screenshot saved to downloads/player_screenshot.png")
        
        print("\n--- All Network Responses ---")
        for resp in responses:
            if 'qqqrst' in resp or 'm3u8' in resp or 'video' in resp or 'ts' in resp or '403' in resp:
                print(resp)
            
        browser.close()

if __name__ == "__main__":
    run()
