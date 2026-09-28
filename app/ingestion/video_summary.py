import os
import sqlite3
import logging
import math
import threading
import time
from pathlib import Path

from langchain_core.documents import Document

from app.cache import (
    CACHE_SCHEMA_VERSION,
    update_video_metadata,
    video_processing_lock,
)
from app.ingestion.transcript import CHUNKING_VERSION, build_chunks
<<<<<<< HEAD
from app.llm.models import get_groq_variant
=======
from app.llm.models import google_llm,groq_llm
>>>>>>> 13da7b824cf1679b856c26d8213f656a276d558e


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "video_summaries.sqlite3"
MAX_SUMMARY_INPUT_CHARS = 12000
SUMMARY_VERSION = f"{CACHE_SCHEMA_VERSION}:{CHUNKING_VERSION}:4"
logger = logging.getLogger(__name__)
_schema_lock = threading.Lock()
_initialized_databases: set[Path] = set()


def _connect() -> sqlite3.Connection:
    database_path = Path(os.getenv("VIDEO_SUMMARY_DB_PATH", str(DEFAULT_DB_PATH)))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=300)
    database_key = database_path.resolve()
    if database_key not in _initialized_databases:
        with _schema_lock:
            if database_key not in _initialized_databases:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS video_summaries (
                        video_id TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        summary_version TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(video_summaries)")
                }
                if "summary_version" not in columns:
                    connection.execute(
                        "ALTER TABLE video_summaries ADD COLUMN summary_version TEXT NOT NULL DEFAULT ''"
                    )
                    connection.execute(
                        "UPDATE video_summaries SET summary_version = 'legacy' WHERE summary_version = ''"
                    )
                connection.commit()
                _initialized_databases.add(database_key)
    return connection


def get_video_summary(video_id: str) -> str | None:
    if not video_id:
        raise ValueError("A video_id is required to retrieve a video summary.")

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT summary FROM video_summaries WHERE video_id = ? AND summary_version = ?",
            (video_id, SUMMARY_VERSION),
        ).fetchone()
    finally:
        connection.close()
    print(f"[Cache] Summary {'HIT' if row else 'MISS'}: {video_id}")
    return row[0] if row else None


def _invoke_summary(prompt: str, transcript: str) -> str:
<<<<<<< HEAD
    model = get_groq_variant("summary")
    response = model.invoke(prompt.format(transcript=transcript))
=======
    response = groq_llm.invoke(prompt.format(transcript=transcript))
>>>>>>> 13da7b824cf1679b856c26d8213f656a276d558e
    content = response.content
    if isinstance(content, str):
        summary = content.strip()
    elif isinstance(content, list):
        summary = " ".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    else:
        summary = str(content).strip()

    if not summary:
        raise RuntimeError("The summary model returned an empty response.")
    return summary


def _group_texts(texts: list[str]) -> list[str]:
    groups: list[str] = []
    current: list[str] = []
    current_length = 0

    for text in texts:
        if current and current_length + len(text) > MAX_SUMMARY_INPUT_CHARS:
            groups.append("\n\n".join(current))
            current = []
            current_length = 0
        current.append(text)
        current_length += len(text)

    if current:
        groups.append("\n\n".join(current))
    return groups


def _format_timestamp(value: object) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 0.0
    if not math.isfinite(seconds) or seconds < 0:
        seconds = 0.0

    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _select_summary_chunks(chunks: list[Document], max_chunks: int = 15) -> list[Document]:
    if len(chunks) <= max_chunks:
        return chunks

    chunk_count = len(chunks)
    selected_indices: set[int] = set()
    for start_fraction, end_fraction in ((0.0, 0.2), (0.4, 0.6), (0.8, 1.0)):
        start = int(chunk_count * start_fraction)
        end = min(chunk_count, int(chunk_count * end_fraction))
        section_indices = range(start, end)
        section_count = min(5, len(section_indices))
        if section_count == 1:
            selected_indices.add(start)
        elif section_count > 1:
            selected_indices.update(
                round(start + offset * (end - start - 1) / (section_count - 1))
                for offset in range(section_count)
            )

    return [chunks[index] for index in sorted(selected_indices)][:max_chunks]


def _generate_video_summary(chunks: list[Document]) -> str:
    selected_chunks = _select_summary_chunks(chunks)
    timestamped_chunks = [
        f"[{_format_timestamp(chunk.metadata.get('start'))}] {chunk.page_content.strip()}"
        for chunk in selected_chunks
        if chunk.page_content.strip()
    ]
    if not timestamped_chunks:
        raise ValueError("The video transcript is empty, so it cannot be summarized.")

    section_prompt = """Summarize this section of a YouTube transcript as concise main-component bullets. Preserve its important concepts, arguments, examples, explanations, and conclusions. Start every bullet with the exact [MM:SS] timestamp of the transcript chunk that supports it. Use only timestamps supplied below; never invent or estimate one. Use only facts stated in the transcript. Do not add outside knowledge.

Transcript section:
{transcript}"""
    final_prompt = """Write a clear video-level summary as a short list of the video's main components. For every component, use a bullet in this form: [MM:SS] Component: explanation. Cover the main topic, important concepts, major arguments, examples, relationships between concepts, and conclusions. Keep the exact timestamps supplied in the transcript; do not invent, estimate, or change timestamps. Combine duplicate components where appropriate. Stay strictly grounded in the transcript and do not add outside knowledge.

Transcript:
{transcript}"""

    full_text = "\n\n".join(timestamped_chunks)
    if len(full_text) <= MAX_SUMMARY_INPUT_CHARS:
        return _invoke_summary(final_prompt, full_text)

    sections = [
        _invoke_summary(section_prompt, section)
        for section in _group_texts(timestamped_chunks)
    ]
    while sum(map(len, sections)) > MAX_SUMMARY_INPUT_CHARS:
        sections = [
            _invoke_summary(section_prompt, group)
            for group in _group_texts(sections)
        ]

    return _invoke_summary(final_prompt, "\n\n".join(sections))


def ensure_video_summary(
    video_id: str,
    chunks: list[Document] | None = None,
) -> str:
    """Return a stored summary, generating and storing it only when absent."""
    if not video_id:
        raise ValueError("A video_id is required to generate a video summary.")

    with video_processing_lock(video_id):
        connection = _connect()
        try:
            # Serialize the missing-summary check and insert across app workers.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT summary FROM video_summaries WHERE video_id = ? AND summary_version = ?",
                (video_id, SUMMARY_VERSION),
            ).fetchone()
            if row:
                connection.commit()
                print(f"[Cache] Summary HIT: {video_id}")
                return row[0]

            print(f"[Cache] Summary MISS: {video_id}")
            started_at = time.perf_counter()
            transcript_chunks = chunks if chunks is not None else build_chunks(video_id)
            summary = _generate_video_summary(transcript_chunks)
            connection.execute(
                """
                INSERT INTO video_summaries (video_id, summary, summary_version)
                VALUES (?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    summary = excluded.summary,
                    summary_version = excluded.summary_version,
                    created_at = CURRENT_TIMESTAMP
                """,
                (video_id, summary, SUMMARY_VERSION),
            )
            connection.commit()
            try:
                update_video_metadata(video_id, {"summary_available": True})
            except OSError:
                logger.exception("Could not update metadata for summary %s", video_id)
            elapsed = time.perf_counter() - started_at
            print(f"[Cache] Summary stored: {video_id} ({elapsed:.2f}s)")
            return summary
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()