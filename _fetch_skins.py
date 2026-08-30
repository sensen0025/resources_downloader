import requests
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

headers = {'User-Agent': 'Mozilla/5.0'}
urls = [
    ('https://littleskin.cn/skinlib/data?filter=skin&sort=likes&keyword=%E6%AD%A6%E5%A3%AB', '武士'),
    ('https://littleskin.cn/skinlib/data?filter=skin&sort=likes&keyword=%E5%8F%A4%E9%A3%8E', '古风'),
    ('https://littleskin.cn/skinlib/data?filter=skin&sort=likes&keyword=%E9%AA%91%E5%A3%AB', '骑士'),
    ('https://littleskin.cn/skinlib/data?filter=skin&sort=likes&keyword=%E6%B5%AA%E4%BA%BA', '浪人'),
]

os.makedirs('downloads/skins', exist_ok=True)
downloaded = []

for u, tag in urls:
    try:
        r = requests.get(u, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json().get('data', {}).get('items', [])
            for item in data[:3]:
                tid = item.get('tid')
                name = item.get('name', 'skin')
                tex_hash = item.get('hash', '')
                if tex_hash:
                    tex_url = f'https://littleskin.cn/textures/{tex_hash}'
                    img_resp = requests.get(tex_url, headers=headers, timeout=10)
                    if img_resp.status_code == 200 and img_resp.content.startswith(b'\x89PNG'):
                        safe_name = ''.join(c for c in name if c.isalnum() or c in (' ', '_', '-', '[\u4e00-\u9fa5]')).strip() or f'skin_{tid}'
                        fn = f'downloads/skins/{safe_name}_{tag}.png'
                        with open(fn, 'wb') as f:
                            f.write(img_resp.content)
                        downloaded.append((name, fn, len(img_resp.content)))
    except Exception as e:
        print(f'fetch {tag} failed: {e}')

print(f'已下载 {len(downloaded)} 款精选 Minecraft 皮肤:')
for n, p, s in downloaded:
    print(f'  - {n} -> {p} ({s} 字节, 标准 PNG 格式)')
