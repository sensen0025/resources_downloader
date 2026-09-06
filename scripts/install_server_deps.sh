#!/bin/bash
set -e

echo "=== [1/5] 安装 yt-dlp ==="
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
chmod a+rx /usr/local/bin/yt-dlp

echo "=== [2/5] 安装 ffmpeg & ffprobe 静态二进制 ==="
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz
mkdir -p /tmp/ffmpeg-static
tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg-static --strip-components=1
cp /tmp/ffmpeg-static/ffmpeg /tmp/ffmpeg-static/ffprobe /usr/local/bin/
chmod a+rx /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-static

echo "=== [3/5] 安装 BBDown ==="
yum install -y unzip || true
curl -L https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240323_linux-x64.zip -o /tmp/bbdown.zip
unzip -o /tmp/bbdown.zip -d /usr/local/bin/
chmod a+rx /usr/local/bin/BBDown
rm -f /tmp/bbdown.zip

echo "=== [4/5] 同步最新网页代码与重启服务 ==="
cd /root/resources_downloader
git pull origin main
systemctl restart resources-web

echo "=== [5/5] 验证工具就绪 ==="
yt-dlp --version || true
ffmpeg -version | head -n 1 || true
BBDown --version || true

echo "=== 全部安装与部署完毕！请刷新网页查看！ ==="
