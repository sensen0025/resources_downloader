import asyncio
import os
import re
import json
import time
import uuid
import sqlite3
import hashlib
import zipfile
import subprocess
from typing import Dict, List, Optional, AsyncGenerator
from web.models import TaskInfo, TaskStatus, DownloadRequest, DownloadType, ProbeResult

DB_PATH = "/home/sensen/resources_downloader/web/tasks.db"
DEFAULT_DOWNLOAD_DIR = "/home/sensen/Downloads"
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            url_or_query TEXT,
            skill_used TEXT,
            status TEXT,
            progress REAL,
            speed_text TEXT,
            eta_text TEXT,
            downloaded_bytes INTEGER,
            total_bytes INTEGER,
            created_at REAL,
            completed_at REAL,
            output_file TEXT,
            probe_json TEXT,
            error_message TEXT,
            logs_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, TaskInfo] = {}
        self.listeners: Dict[str, List[asyncio.Queue]] = {}
        self.global_listeners: List[asyncio.Queue] = []
        self._load_history()

    def _load_history(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100')
            for row in c.fetchall():
                probe_data = json.loads(row[13]) if row[13] else None
                probe_obj = ProbeResult(**probe_data) if probe_data else None
                logs = json.loads(row[15]) if row[15] else []
                t = TaskInfo(
                    id=row[0],
                    title=row[1] or "未命名任务",
                    url_or_query=row[2] or "",
                    skill_used=row[3] or "generic",
                    status=TaskStatus(row[4]) if row[4] in TaskStatus._value2member_map_ else TaskStatus.COMPLETED,
                    progress=row[5] or 0.0,
                    speed_text=row[6] or "",
                    eta_text=row[7] or "",
                    downloaded_bytes=row[8] or 0,
                    total_bytes=row[9] or 0,
                    created_at=row[10] or time.time(),
                    completed_at=row[11],
                    output_file=row[12],
                    probe=probe_obj,
                    error_message=row[14],
                    logs=logs[-100:]
                )
                self.tasks[t.id] = t
            conn.close()
        except Exception as e:
            print("Failed to load task history:", e)

    def _save_task(self, task: TaskInfo):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            probe_json = task.probe.model_dump_json() if task.probe else None
            logs_json = json.dumps(task.logs[-200:])
            c.execute('''
                INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                task.id, task.title, task.url_or_query, task.skill_used,
                task.status.value, task.progress, task.speed_text, task.eta_text,
                task.downloaded_bytes, task.total_bytes, task.created_at,
                task.completed_at, task.output_file, probe_json,
                task.error_message, logs_json
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB save error:", e)

    def broadcast(self, task: TaskInfo):
        self._save_task(task)
        data = task.model_dump_json()
        # Notify task specific listeners
        if task.id in self.listeners:
            for q in list(self.listeners[task.id]):
                try:
                    q.put_nowait(data)
                except Exception:
                    pass
        # Notify global listeners
        for q in list(self.global_listeners):
            try:
                q.put_nowait(data)
            except Exception:
                pass

    async def add_listener(self, task_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        q = asyncio.Queue()
        if task_id:
            if task_id not in self.listeners:
                self.listeners[task_id] = []
            self.listeners[task_id].append(q)
        else:
            self.global_listeners.append(q)
            
        try:
            while True:
                data = await q.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            if task_id and task_id in self.listeners and q in self.listeners[task_id]:
                self.listeners[task_id].remove(q)
            elif q in self.global_listeners:
                self.global_listeners.remove(q)

    def probe_file_integrity(self, file_path: str) -> Optional[ProbeResult]:
        if not os.path.exists(file_path) or os.path.isdir(file_path):
            return None
        size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            head = f.read(16)
            f.seek(0)
            sha256 = hashlib.sha256(f.read()).hexdigest()
        
        magic_hex = head[:4].hex()
        is_valid = size > 0
        format_name = "Binary / Unknown"
        mime_type = "application/octet-stream"
        sample_info = None

        if magic_hex == "504b0304": # Zip / EPUB / DOCX
            try:
                with zipfile.ZipFile(file_path, 'r') as zf:
                    if 'mimetype' in zf.namelist():
                        mt = zf.read('mimetype').decode('utf-8', errors='ignore').strip()
                        if 'epub' in mt:
                            format_name = "EPUB E-Book"
                            mime_type = "application/epub+zip"
                    else:
                        format_name = "ZIP Archive"
                        mime_type = "application/zip"
                    sample_info = f"Contains {len(zf.namelist())} files: {', '.join(zf.namelist()[:4])}"
            except Exception:
                format_name = "Incomplete Zip"
                is_valid = False
        elif magic_hex.startswith("25504446"): # %PDF
            format_name = "PDF Document"
            mime_type = "application/pdf"
        elif head[4:8] == b'ftyp' or head[4:8] == b'moov': # MP4
            format_name = "MP4 Video"
            mime_type = "video/mp4"
        elif magic_hex == "1a45dfa3": # WebM / Matroska
            format_name = "WebM / MKV Video"
            mime_type = "video/webm"
        elif magic_hex.startswith("494433") or magic_hex.startswith("fffb"): # MP3
            format_name = "MP3 Audio"
            mime_type = "audio/mpeg"
        elif magic_hex == "89504e47": # PNG
            format_name = "PNG Image"
            mime_type = "image/png"
        elif magic_hex.startswith("ffd8ff"): # JPEG
            format_name = "JPEG Image"
            mime_type = "image/jpeg"

        return ProbeResult(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            file_size=size,
            magic_hex=magic_hex,
            mime_type=mime_type,
            sha256=sha256,
            is_valid=is_valid,
            format_name=format_name,
            sample_info=sample_info
        )

    def detect_type(self, target: str, explicit: DownloadType) -> DownloadType:
        if explicit and explicit != DownloadType.AUTO:
            return explicit
        t = target.strip().lower()
        if "youtube.com" in t or "youtu.be" in t:
            return DownloadType.YOUTUBE
        elif "bilibili.com" in t or "b23.tv" in t or re.match(r'^(bv[0-9a-za-z]+|av[0-9]+)$', t, re.I):
            return DownloadType.BILIBILI
        elif ".m3u8" in t:
            return DownloadType.HLS
        elif "annas-archive" in t or "libgen" in t or t.endswith(".epub") or t.endswith(".pdf"):
            return DownloadType.ANNAS_ARCHIVE
        elif t.startswith("http://") or t.startswith("https://"):
            return DownloadType.DIRECT
        else:
            # Default to Anna's Archive book search if query is non-URL text
            return DownloadType.ANNAS_ARCHIVE

    async def submit_task(self, req: DownloadRequest) -> TaskInfo:
        dtype = self.detect_type(req.url_or_query, req.download_type)
        task_id = str(uuid.uuid4())[:8]
        
        skill_names = {
            DownloadType.YOUTUBE: "site-videos-yt-dlp",
            DownloadType.BILIBILI: "site-bilibili-bbdown",
            DownloadType.ANNAS_ARCHIVE: "site-annas-archive",
            DownloadType.HLS: "download_hls",
            DownloadType.DIRECT: "download_file"
        }
        
        title = req.output_name or req.url_or_query[:60]
        task = TaskInfo(
            id=task_id,
            title=title,
            url_or_query=req.url_or_query,
            skill_used=skill_names.get(dtype, "generic"),
            status=TaskStatus.QUEUED,
            progress=0.0
        )
        self.tasks[task_id] = task
        self.broadcast(task)
        
        asyncio.create_task(self._run_task(task_id, req, dtype))
        return task

    async def _run_task(self, task_id: str, req: DownloadRequest, dtype: DownloadType):
        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        self.broadcast(task)

        env = os.environ.copy()
        env["PATH"] = f"/home/sensen/bin:{env.get('PATH', '')}"

        try:
            if dtype == DownloadType.YOUTUBE:
                await self._exec_youtube(task, req, env)
            elif dtype == DownloadType.BILIBILI:
                await self._exec_bilibili(task, req, env)
            elif dtype == DownloadType.ANNAS_ARCHIVE:
                await self._exec_annas_archive(task, req, env)
            elif dtype == DownloadType.HLS:
                await self._exec_hls(task, req, env)
            else:
                await self._exec_direct(task, req, env)
                
            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.completed_at = time.time()
            if task.output_file and os.path.exists(task.output_file):
                task.probe = self.probe_file_integrity(task.output_file)
                task.downloaded_bytes = task.probe.file_size if task.probe else os.path.getsize(task.output_file)
                task.total_bytes = task.downloaded_bytes
            task.logs.append(f"🎉 任务完成！产物文件: {task.output_file}")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            task.completed_at = time.time()
            task.logs.append(f"❌ 执行失败: {e}")
            
        self.broadcast(task)

    async def _exec_process(self, task: TaskInfo, cmd: List[str], env: dict, line_parser=None):
        task.logs.append(f"⚡ 执行命令: {' '.join(cmd)}")
        self.broadcast(task)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )
        
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode('utf-8', errors='ignore').strip()
            if line:
                task.logs.append(line)
                if line_parser:
                    line_parser(line, task)
                self.broadcast(task)
                
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"命令执行失败，退出码: {rc}")

    async def _exec_youtube(self, task: TaskInfo, req: DownloadRequest, env: dict):
        out_tpl = os.path.join(DEFAULT_DOWNLOAD_DIR, "youtube", "%(title)s.%(ext)s")
        os.makedirs(os.path.dirname(out_tpl), exist_ok=True)
        
        cmd = ["yt-dlp", "--js-runtimes", "node", "--newline", "-o", out_tpl]
        if req.format_option == "audio_only":
            cmd.extend(["-x", "--audio-format", "mp3", "--audio-quality", "0"])
        else:
            cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
            
        if req.subtitles:
            cmd.extend(["--write-subs", "--write-auto-subs", "--sub-lang", "zh-Hans,zh,en", "--convert-subs", "srt"])
            
        cmd.append(req.url_or_query)
        
        def parse_ytdlp(line, t):
            # [download]  45.2% of 25.10MiB at  2.45MiB/s ETA 00:05
            m = re.search(r'\[download\]\s+([\d\.]+)%\s+of\s+~?([\d\.]+[A-Za-z]+)\s+at\s+([\d\.]+[A-Za-z]+/s)\s+ETA\s+([\d:]+)', line)
            if m:
                t.progress = float(m.group(1))
                t.speed_text = m.group(3)
                t.eta_text = m.group(4)
            m_dest = re.search(r'\[download\] Destination: (.+)', line) or re.search(r'\[Merger\] Merging formats into "(.+)"', line)
            if m_dest:
                t.output_file = m_dest.group(1).strip()
                t.title = os.path.basename(t.output_file)

        await self._exec_process(task, cmd, env, parse_ytdlp)

    async def _exec_bilibili(self, task: TaskInfo, req: DownloadRequest, env: dict):
        out_dir = os.path.join(DEFAULT_DOWNLOAD_DIR, "bilibili")
        os.makedirs(out_dir, exist_ok=True)
        
        cmd = ["BBDown", "--work-dir", out_dir, "-hs"]
        if req.format_option == "audio_only":
            cmd.append("--audio-only")
        cmd.append(req.url_or_query)
        
        def parse_bbdown(line, t):
            m_title = re.search(r'视频标题: (.+)', line)
            if m_title:
                t.title = m_title.group(1).strip()
            # Look for progress
            m_prog = re.search(r'([\d\.]+)%\s+([0-9\.]+\s+[KMGT]?B/s)', line)
            if m_prog:
                t.progress = float(m_prog.group(1))
                t.speed_text = m_prog.group(2)
            if "任务完成" in line or "下载完成" in line or ".mp4" in line:
                for f in os.listdir(out_dir):
                    if f.endswith(".mp4") or f.endswith(".m4a") or f.endswith(".flv"):
                        t.output_file = os.path.join(out_dir, f)

        await self._exec_process(task, cmd, env, parse_bbdown)

    async def _exec_annas_archive(self, task: TaskInfo, req: DownloadRequest, env: dict):
        out_dir = os.path.join(DEFAULT_DOWNLOAD_DIR, "books")
        os.makedirs(out_dir, exist_ok=True)
        task.logs.append(f"📚 启动 Anna's Archive 浏览器引擎过盾与检索: {req.url_or_query}")
        self.broadcast(task)

        script = f"""
from playwright.sync_api import sync_playwright
import time, re, subprocess, os, json

query = {json.dumps(req.url_or_query)}
out_dir = {json.dumps(out_dir)}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{{get:()=>undefined}})")
    page = ctx.new_page()

    print("STATUS:10% 连接 Anna's Archive (https://annas-archive.gd/)...")
    page.goto("https://annas-archive.gd/", timeout=35000)
    page.wait_for_selector('input[name="q"]', timeout=20000)
    print("STATUS:25% DDoS-Guard 校验通过，提交检索...")

    if query.startswith("http://") or query.startswith("https://"):
        page.goto(query, timeout=35000)
    else:
        with page.expect_navigation():
            page.fill('input[name="q"]', query)
            page.keyboard.press("Enter")
    
    for _ in range(12):
        time.sleep(1.5)
        if 'DDoS' not in page.title() and 'check=' not in page.url:
            break

    links = page.query_selector_all('a[href*="/md5/"]')
    if not links:
        print("ERROR: 未找到匹配电子书条目")
        browser.close()
        exit(1)

    md5_list = []
    for l in links[:6]:
        h = l.get_attribute('href') or ''
        if '/md5/' in h:
            md5_list.append(h.split('/md5/')[-1].strip('/'))

    print(f"STATUS:45% 发现 {{len(md5_list)}} 个候选 MD5，解析慢速节点直链...")
    
    downloaded_file = None
    for md5 in md5_list:
        if downloaded_file: break
        for p_idx in range(8):
            slow_url = f"https://annas-archive.gd/slow_download/{{md5}}/0/{{p_idx}}"
            page.goto(slow_url, timeout=30000)
            for _ in range(8):
                time.sleep(1)
                if 'DDoS' not in page.title() and 'Checking' not in page.title():
                    break
            
            dl_a = page.query_selector_all('a[href*="anon/s/"], a[href*=".epub"], a[href*=".pdf"], a[href*=".mobi"]')
            if dl_a:
                direct_url = dl_a[0].get_attribute('href')
                print(f"STATUS:70% 命中合作服务器 #{{p_idx}} 直链: {{direct_url[:60]}}...")
                temp_bin = os.path.join(out_dir, "temp_book.bin")
                if os.path.exists(temp_bin): os.remove(temp_bin)
                subprocess.run([
                    'curl', '-L', '--max-time', '60',
                    '-A', 'Mozilla/5.0 (X11; Linux x86_64)',
                    '-H', 'Referer: https://annas-archive.gd/',
                    '-o', temp_bin, direct_url
                ])
                if os.path.exists(temp_bin) and os.path.getsize(temp_bin) > 1000:
                    magic = open(temp_bin, 'rb').read(4).hex()
                    ext = ".epub" if magic == "504b0304" else ".pdf" if magic.startswith("25504446") else ".bin"
                    safe_name = re.sub(r'[^\\w\\-\\.\\u4e00-\\u9fa5]', '_', query)[:40] + ext
                    final_path = os.path.join(out_dir, safe_name)
                    os.rename(temp_bin, final_path)
                    downloaded_file = final_path
                    print(f"DELIVERABLE:{{final_path}}")
                    break
    browser.close()
"""
        cmd = ["python3", "-c", script]
        
        def parse_aa(line, t):
            if line.startswith("STATUS:"):
                m_pct = re.search(r'STATUS:(\d+)%', line)
                if m_pct:
                    t.progress = float(m_pct.group(1))
            elif line.startswith("DELIVERABLE:"):
                t.output_file = line.split("DELIVERABLE:")[-1].strip()
                t.title = os.path.basename(t.output_file)
                t.progress = 100.0

        await self._exec_process(task, cmd, env, parse_aa)

    async def _exec_direct(self, task: TaskInfo, req: DownloadRequest, env: dict):
        out_dir = os.path.join(DEFAULT_DOWNLOAD_DIR, "files")
        os.makedirs(out_dir, exist_ok=True)
        filename = req.output_name or os.path.basename(req.url_or_query.split("?")[0]) or "downloaded_file.bin"
        out_path = os.path.join(out_dir, filename)
        task.output_file = out_path
        task.title = filename

        cmd = [
            "curl", "-L", "-C", "-",
            "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "-o", out_path,
            req.url_or_query
        ]
        
        def parse_curl(line, t):
            # Parse curl standard progress bar if present
            m = re.search(r'([\d\.]+)%\s+([\d\.]+[A-Za-z]+)\s+([\d\.]+[A-Za-z]+/s)', line)
            if m:
                t.progress = float(m.group(1))
                t.speed_text = m.group(3)

        await self._exec_process(task, cmd, env, parse_curl)

    async def _exec_hls(self, task: TaskInfo, req: DownloadRequest, env: dict):
        out_dir = os.path.join(DEFAULT_DOWNLOAD_DIR, "videos")
        os.makedirs(out_dir, exist_ok=True)
        filename = req.output_name or "stream_video.mp4"
        if not filename.endswith(".mp4"):
            filename += ".mp4"
        out_path = os.path.join(out_dir, filename)
        task.output_file = out_path
        task.title = filename

        cmd = [
            "ffmpeg", "-y", "-i", req.url_or_query,
            "-c", "copy", "-bsf:a", "aac_adtstoasc",
            out_path
        ]
        await self._exec_process(task, cmd, env)

task_manager = TaskManager()
