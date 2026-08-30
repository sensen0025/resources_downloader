import requests
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

skin_slugs = [
    ("midnightwarrior", "暗夜武士_MidnightWarrior"),
    ("goldenwarrior", "黄金古士_GoldenWarrior"),
    ("spartanwarrior", "斯巴达勇士_SpartanWarrior"),
    ("goldcrusader", "黄金十字军骑士_GoldCrusader"),
    ("knight", "古典骑士_Knight"),
    ("samurai", "东洋武士_Samurai"),
    ("paladin", "圣殿骑士_Paladin"),
    ("fallenangel", "堕落天使_FallenAngel"),
    ("assassin", "刺客浪人_Assassin")
]

os.makedirs('downloads/skins', exist_ok=True)
downloaded = []

for slug, label in skin_slugs:
    dl_url = f'https://www.minecraftskins.net/{slug}/download'
    try:
        r = requests.get(dl_url, headers=headers, timeout=12)
        if r.status_code == 200 and r.content.startswith(b'\x89PNG'):
            file_path = f'downloads/skins/{label}.png'
            with open(file_path, 'wb') as f:
                f.write(r.content)
            downloaded.append((label, file_path, len(r.content)))
            print(f"✅ 下载成功: [{label}] -> {file_path} ({len(r.content)} 字节, 标准 MC 皮肤 PNG)")
        else:
            print(f"⚠️ {slug} 返回状态: {r.status_code}")
    except Exception as e:
        print(f"❌ {slug} 下载失败: {e}")

print(f"\n🎉 共下载 {len(downloaded)} 款相关 Minecraft 皮肤到 downloads/skins/ 目录！")
