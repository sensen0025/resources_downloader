"""站点信誉库存储 — LLM 打分的站点经验库 + 纯 Python 哈希向量相似度检索。

条目(按 host 主键):
- score: 0-9(9 = 内容易获取且真实;综合 获取容易度/虚假/可访问性/登录墙/实际落地)
- description: LLM 生成的一句中文短描述(≤60 字)
- query: 触发该站点访问的任务查询
- outcome / note: 最近一次交互结果与备注
- visit_count / first_seen / last_seen: 访问统计
- embedding: host+描述+查询 的字符 n-gram 哈希向量(纯 Python,零依赖)

向量相似度:字符 2-4-gram → crc32 稳定哈希到 256 维(符号随机化),
L2 归一化后余弦 = 点积。无 numpy/模型依赖,服务器可直接跑。
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import zlib
from pathlib import Path
from typing import Optional

__all__ = [
    "DEFAULT_PATH", "embed_text", "load", "save", "record", "lookup",
    "score_of", "entries", "clear",
]

DEFAULT_PATH = "data/site_reputation.json"
_DIM = 256
_lock = threading.Lock()


def store_path() -> Path:
    return Path(os.environ.get("RH_SITEREP_PATH", DEFAULT_PATH))


# ---------------------------------------------------------------- 向量嵌入

def embed_text(text: str) -> list[float]:
    """字符 n-gram 哈希向量(2-4 gram,crc32 稳定哈希 + 符号随机化),L2 归一化。"""
    s = (text or "").lower()
    v = [0.0] * _DIM
    for n in (2, 3, 4):
        for i in range(len(s) - n + 1):
            h = zlib.crc32(s[i:i + n].encode("utf-8"))
            v[h % _DIM] += 1.0 if (h >> 31) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norm, 6) for x in v]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return round(sum(x * y for x, y in zip(a, b)), 4)


def _entry_text(e: dict) -> str:
    return " ".join(filter(None, [
        e.get("host", ""), e.get("description", ""), e.get("query", ""),
        e.get("outcome", ""), e.get("tags", ""),
    ]))


# ---------------------------------------------------------------- 读写

_load_cache: dict = {"mtime": 0.0, "data": {}}


def load() -> dict[str, dict]:
    """读取信誉库(带 mtime 缓存:库增大后避免每次解析整份 JSON)。"""
    p = store_path()
    try:
        mtime = p.stat().st_mtime if p.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _load_cache["mtime"] == mtime:
        return _load_cache["data"]
    data: dict[str, dict] = {}
    try:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except Exception:
        data = {}
    _load_cache["mtime"] = mtime
    _load_cache["data"] = data
    return data


def save(entries: dict[str, dict]) -> None:
    p = store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # 只读环境/磁盘满:记录失败不阻塞任务
    try:
        _load_cache["mtime"] = p.stat().st_mtime
        _load_cache["data"] = entries
    except OSError:
        pass


def entries() -> dict[str, dict]:
    return load()


def clear() -> None:
    """清空库(测试/重置用)。"""
    with _lock:
        p = store_path()
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        _load_cache["mtime"] = 0.0
        _load_cache["data"] = {}


# ---------------------------------------------------------------- 录入

def record(host: str, url: str = "", score: Optional[int] = None,
           description: str = "", query: str = "", outcome: str = "",
           note: str = "", tags: str = "") -> dict:
    """录入/更新一个站点。score=None 时保留旧分(首录则用启发式默认 4)。"""
    host = (host or "").strip().lower()
    if not host:
        return {}
    with _lock:
        entries_map = load()
        e = entries_map.get(host, {})
        now = time.time()
        e["host"] = host
        if url:
            e["url"] = url
        if score is not None:
            e["score"] = max(0, min(9, int(score)))
        elif "score" not in e:
            e["score"] = 4
        if description:
            e["description"] = str(description)[:120]
        if query:
            e["query"] = str(query)[:200]
        if outcome:
            e["outcome"] = str(outcome)[:40]
        if note:
            e["note"] = str(note)[:200]
        if tags:
            e["tags"] = str(tags)[:200]
        e["visit_count"] = int(e.get("visit_count", 0)) + 1
        e.setdefault("first_seen", now)
        e["last_seen"] = now
        e["embedding"] = embed_text(_entry_text(e))
        entries_map[host] = e
        save(entries_map)
        return e


# ---------------------------------------------------------------- 检索

def score_of(host: str) -> Optional[int]:
    """已知站点的历史评分(0-9);未录入返回 None。

    支持子域回退:录入的是注册域(zhihu.com),搜索/分析命中 www.zhihu.com
    也能匹配(逐级缩短子域查找)。
    """
    host = (host or "").strip().lower()
    entries_map = load()
    if host in entries_map and "score" in entries_map[host]:
        return entries_map[host]["score"]
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        e = entries_map.get(cand)
        if e and "score" in e:
            return e["score"]
    return None


def lookup(query: str, top_k: int = 5, min_score: int = 0) -> list[dict]:
    """按向量相似度返回 top-k 站点(附 score/description/similarity)。

    min_score>0 时只返回评分 ≥ min_score 的条目(过滤已知差站)。
    """
    qv = embed_text(query or "")
    scored = []
    for e in load().values():
        if min_score and (e.get("score") is None or int(e.get("score", 0)) < min_score):
            continue
        sim = _cosine(qv, e.get("embedding") or [])
        scored.append({**e, "similarity": sim})
    scored.sort(key=lambda x: (-x["similarity"], -(x.get("score") or 0)))
    return scored[: max(1, min(top_k, 20))]
