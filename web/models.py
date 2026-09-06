from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
import time

class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class DownloadType(str, Enum):
    AUTO = "auto"
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"
    ANNAS_ARCHIVE = "annas_archive"
    DIRECT = "direct"
    HLS = "hls"

class DownloadRequest(BaseModel):
    url_or_query: str = Field(..., description="Target URL or keyword query")
    download_type: Optional[DownloadType] = DownloadType.AUTO
    format_option: Optional[str] = "best" # "best", "audio_only", "1080p", "720p", "epub", "pdf"
    output_name: Optional[str] = None
    subtitles: Optional[bool] = False
    custom_args: Optional[str] = None

class ProbeResult(BaseModel):
    file_path: str
    file_name: str
    file_size: int
    magic_hex: str
    mime_type: str
    sha256: str
    is_valid: bool
    format_name: str
    sample_info: Optional[str] = None

class TaskInfo(BaseModel):
    id: str
    title: str
    url_or_query: str
    skill_used: str
    status: TaskStatus
    progress: float = 0.0 # 0.0 to 100.0
    speed_text: str = ""
    eta_text: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    output_file: Optional[str] = None
    probe: Optional[ProbeResult] = None
    error_message: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
