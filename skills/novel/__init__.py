"""小说类技能 — 章节爬虫拼接器(规则引擎,无 LLM 依赖)。"""

from .merger import (
    clean_book_title,
    extract_chapter_links,
    extract_chapter_text,
    fetch_novel_txt,
)

__all__ = [
    "fetch_novel_txt",
    "extract_chapter_links",
    "extract_chapter_text",
    "clean_book_title",
]
