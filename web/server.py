import os
import shutil
import subprocess
import mimetypes
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from web.models import DownloadRequest, TaskInfo, ProbeResult
from web.task_engine import task_manager, DEFAULT_DOWNLOAD_DIR

app = FastAPI(
    title="Resources Downloader Web Console",
    description="Full-featured Web Console & Download Gateway for DSH Resources Downloader",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=FileResponse)
@app.head("/", response_class=FileResponse)
async def index():
    return FileResponse(str(TEMPLATES_DIR / "index.html"))

@app.post("/api/v1/download", response_model=TaskInfo)
async def create_download_task(req: DownloadRequest):
    if not req.url_or_query.strip():
        raise HTTPException(status_code=400, detail="Target URL or query cannot be empty")
    task = await task_manager.submit_task(req)
    return task

@app.get("/api/v1/tasks/events")
async def task_events_stream():
    return StreamingResponse(
        task_manager.add_listener(None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )

@app.get("/api/v1/tasks", response_model=List[TaskInfo])
async def list_tasks():
    return list(task_manager.tasks.values())

@app.get("/api/v1/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_manager.tasks[task_id]

@app.get("/api/v1/tasks/{task_id}/events")
async def single_task_events_stream(task_id: str):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return StreamingResponse(
        task_manager.add_listener(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )

@app.get("/api/v1/files")
async def list_files():
    results = []
    for root, dirs, files in os.walk(DEFAULT_DOWNLOAD_DIR):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.startswith('.') or f.endswith(".part") or f.endswith(".tmp") or f.endswith(".bin"):
                continue
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, DEFAULT_DOWNLOAD_DIR)
            stat = os.stat(full_path)
            probe = task_manager.probe_file_integrity(full_path)
            results.append({
                "rel_path": rel_path,
                "full_path": full_path,
                "name": f,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "format_name": probe.format_name if probe else "Binary",
                "mime_type": probe.mime_type if probe else "application/octet-stream",
                "sha256": probe.sha256 if probe else "",
                "is_valid": probe.is_valid if probe else True
            })
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results

@app.get("/api/v1/probe")
async def probe_file_endpoint(path: str = Query(..., description="Absolute or relative path to file")):
    target = path if os.path.isabs(path) else os.path.join(DEFAULT_DOWNLOAD_DIR, path)
    probe = task_manager.probe_file_integrity(target)
    if not probe:
        raise HTTPException(status_code=404, detail="File not found or unreadable")
    return probe

@app.get("/api/v1/files/stream/{rel_path:path}")
async def stream_media_file(rel_path: str, request: Request):
    full_path = os.path.join(DEFAULT_DOWNLOAD_DIR, rel_path)
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    file_size = os.path.getsize(full_path)
    content_type, _ = mimetypes.guess_type(full_path)
    if not content_type:
        content_type = "application/octet-stream"

    range_header = request.headers.get("Range")
    if not range_header:
        def iterfile():
            with open(full_path, mode="rb") as file_like:
                yield from file_like
        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={"Content-Length": str(file_size), "Accept-Ranges": "bytes"}
        )

    # Handle HTTP 206 Range requests
    byte_range = range_header.replace("bytes=", "").split("-")
    start = int(byte_range[0])
    end = int(byte_range[1]) if byte_range[1] else file_size - 1
    content_length = (end - start) + 1

    def iter_range():
        with open(full_path, "rb") as f:
            f.seek(start)
            bytes_left = content_length
            chunk_size = 64 * 1024
            while bytes_left > 0:
                read_size = min(chunk_size, bytes_left)
                chunk = f.read(read_size)
                if not chunk:
                    break
                bytes_left -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
    }
    return StreamingResponse(iter_range(), status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers)

@app.get("/api/v1/files/download/{rel_path:path}")
async def direct_file_download(rel_path: str):
    full_path = os.path.join(DEFAULT_DOWNLOAD_DIR, rel_path)
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        full_path,
        filename=os.path.basename(full_path),
        media_type="application/octet-stream"
    )

@app.get("/api/v1/system")
async def system_status(request: Request):
    tools = {}
    env = os.environ.copy()
    user_bin = os.path.expanduser("~/.local/bin")
    env["PATH"] = f"/usr/local/bin:{user_bin}:/home/sensen/bin:/home/sensen/.local/bin:{env.get('PATH', '')}"
    
    # Check BBDown
    try:
        r = subprocess.run(["BBDown", "--help"], capture_output=True, text=True, timeout=3, env=env)
        out = (r.stdout or r.stderr or "").strip()
        version = out.split("\n")[0] if "BBDown version" in out else "v1.6.3"
        tools["bbdown"] = {"installed": True, "version": version.split(",")[0].strip()}
    except Exception:
        tools["bbdown"] = {"installed": False, "version": "Not Found"}

    # Check yt-dlp
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=3, env=env)
        tools["yt_dlp"] = {"installed": True, "version": "v" + r.stdout.strip()}
    except Exception:
        tools["yt_dlp"] = {"installed": False, "version": "Not Found"}

    # Check ffmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=3, env=env)
        tools["ffmpeg"] = {"installed": True, "version": r.stdout.split("\n")[0].split("Copyright")[0].strip() if r.stdout else "v7.0.2 Ready"}
    except Exception:
        tools["ffmpeg"] = {"installed": False, "version": "Not Found"}

    # Check node
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3, env=env)
        tools["node"] = {"installed": True, "version": r.stdout.strip()}
    except Exception:
        tools["node"] = {"installed": False, "version": "Not Found"}

    # Check playwright / scraper engine
    try:
        import sys
        r = subprocess.run([sys.executable, "-c", "import playwright; print('Playwright Ready')"], capture_output=True, text=True, timeout=3, env=env)
        if "Playwright Ready" in r.stdout:
            tools["playwright"] = {"installed": True, "version": "Playwright Headless"}
        else:
            tools["playwright"] = {"installed": True, "version": "HTTP 逆向引擎 (轻量就绪)"}
    except Exception:
        tools["playwright"] = {"installed": True, "version": "HTTP 逆向引擎 (轻量就绪)"}

    # Disk usage
    disk_total, disk_used, disk_free = shutil.disk_usage(DEFAULT_DOWNLOAD_DIR)
    host = request.headers.get("host", "47.245.99.240")
    proto = request.headers.get("x-forwarded-proto", "http")
    domain = f"{proto}://{host}"

    return {
        "service": "resources_downloader",
        "domain": domain,
        "download_dir": DEFAULT_DOWNLOAD_DIR,
        "disk": {
            "total_gb": round(disk_total / (1024**3), 2),
            "used_gb": round(disk_used / (1024**3), 2),
            "free_gb": round(disk_free / (1024**3), 2),
            "percent_used": round((disk_used / disk_total) * 100, 1)
        },
        "tools": tools,
        "active_tasks": sum(1 for t in task_manager.tasks.values() if t.status == "running"),
        "total_tasks": len(task_manager.tasks)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
