import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode
from urllib.request import urlopen


CACHE_SCHEMA_VERSION = 1
MAX_CACHED_VIDEOS = 5
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "data" / "video_cache"

_lock_guard = threading.Lock()
_video_locks: dict[str, tuple[threading.RLock, int]] = {}
_video_titles: dict[str, str] = {}


def cache_root() -> Path:
    return Path(os.getenv("VIDEO_CACHE_DIR", str(DEFAULT_CACHE_DIR)))


def _video_id_hash(video_id: str) -> str:
    video_key = hashlib.sha256(video_id.encode("utf-8")).hexdigest()
    return video_key


def video_cache_dir(video_id: str, video_title: str | None = None) -> Path:
    return cache_root() / _video_id_hash(video_id)


def _cache_directory_video_id(directory: Path) -> str:
    metadata = read_json(directory / "metadata.json") or {}
    pointer = read_json(directory / "title.json") or {}
    video_id = metadata.get("video_id") or pointer.get("video_id")
    if video_id:
        return str(video_id)

    _, separator, suffix = directory.name.rpartition("--")
    return suffix if separator else f"unknown:{directory.name}"


def _video_cache_directories(video_id: str) -> list[Path]:
    root = cache_root()
    if not root.is_dir():
        return []
    return [
        path
        for path in root.iterdir()
        if path.is_dir() and _cache_directory_video_id(path) == video_id
    ]


def _merge_cache_directory(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source_path in source.iterdir():
        destination_path = destination / source_path.name
        if source_path.name == "title.json":
            source_path.unlink()
        elif source_path.is_dir():
            if destination_path.is_dir():
                _merge_cache_directory(source_path, destination_path)
            elif not destination_path.exists():
                os.replace(source_path, destination_path)
            else:
                shutil.rmtree(source_path)
        elif source_path.name == "metadata.json" and destination_path.is_file():
            source_metadata = read_json(source_path) or {}
            destination_metadata = read_json(destination_path) or {}
            write_json_atomic(
                destination_path,
                {**source_metadata, **destination_metadata},
            )
            source_path.unlink()
        elif not destination_path.exists() or source_path.stat().st_mtime > destination_path.stat().st_mtime:
            os.replace(source_path, destination_path)
        else:
            source_path.unlink()
    shutil.rmtree(source, ignore_errors=True)


def prune_video_cache(protected_video_ids: set[str] | None = None) -> None:
    root = cache_root()
    if not root.is_dir():
        return

    groups: dict[str, list[Path]] = {}
    last_access: dict[str, float] = {}
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        video_id = _cache_directory_video_id(directory)
        metadata = read_json(directory / "metadata.json") or {}
        try:
            accessed = float(metadata.get("last_accessed_at", directory.stat().st_mtime))
        except (TypeError, ValueError, OSError):
            accessed = directory.stat().st_mtime
        groups.setdefault(video_id, []).append(directory)
        last_access[video_id] = max(last_access.get(video_id, 0.0), accessed)

    with _lock_guard:
        protected = set(protected_video_ids or ()) | set(_video_locks)

    while len(groups) > MAX_CACHED_VIDEOS:
        candidates = [video_id for video_id in groups if video_id not in protected]
        if not candidates:
            break
        oldest_video_id = min(candidates, key=lambda video_id: last_access[video_id])
        for directory in groups.pop(oldest_video_id):
            try:
                shutil.rmtree(directory)
            except OSError:
                continue
        last_access.pop(oldest_video_id, None)
        print(f"[Cache] Evicted video cache: {oldest_video_id}")


def prepare_video_cache(video_id: str, video_title: str | None = None) -> str:
    """Resolve a display title and consolidate caches under the stable video ID."""
    if not video_id:
        raise ValueError("A video_id is required to prepare its cache.")

    with video_processing_lock(video_id):
        supplied_title = (video_title or "").strip()
        cache_root().mkdir(parents=True, exist_ok=True)
        canonical_directory = video_cache_dir(video_id)
        with _lock_guard:
            process_title = _video_titles.get(video_id, "")
        existing_directories = _video_cache_directories(video_id)
        existing_metadata = {}
        for directory in existing_directories:
            existing_metadata = read_json(directory / "metadata.json") or {}
            if existing_metadata.get("title"):
                break
        legacy_pointer = read_json(canonical_directory / "title.json") or {}
        title = (
            supplied_title
            or process_title
            or str(existing_metadata.get("title") or legacy_pointer.get("title") or "").strip()
        )

        if not title:
            try:
                query = urlencode({
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "format": "json",
                })
                with urlopen(
                    f"https://www.youtube.com/oembed?{query}", timeout=4
                ) as response:
                    title = str(json.load(response).get("title") or "").strip()
            except Exception:
                title = ""
        title = title or video_id

        for directory in existing_directories:
            if directory != canonical_directory and directory.exists():
                _merge_cache_directory(directory, canonical_directory)

        with _lock_guard:
            _video_titles[video_id] = title

        metadata = read_json(canonical_directory / "metadata.json") or {}
        metadata.update(
            {
                "video_id": video_id,
                "title": title,
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "last_accessed_at": time.time(),
            }
        )
        write_json_atomic(canonical_directory / "metadata.json", metadata)
    prune_video_cache(protected_video_ids={video_id})
    return title


@contextmanager
def video_processing_lock(video_id: str) -> Iterator[None]:
    """Serialize processing for one video without blocking other videos."""
    with _lock_guard:
        lock, users = _video_locks.get(video_id, (threading.RLock(), 0))
        _video_locks[video_id] = (lock, users + 1)

    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _lock_guard:
            current_lock, users = _video_locks[video_id]
            if users == 1:
                del _video_locks[video_id]
            else:
                _video_locks[video_id] = (current_lock, users - 1)


def read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
            temp_path = Path(file.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def update_video_metadata(
    video_id: str,
    values: dict,
    video_title: str | None = None,
) -> None:
    with video_processing_lock(video_id):
        directory = video_cache_dir(video_id, video_title)
        path = directory / "metadata.json"
        metadata = read_json(path) or {
            "video_id": video_id,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
        }
        if metadata.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            metadata = {
                "video_id": video_id,
                "cache_schema_version": CACHE_SCHEMA_VERSION,
            }
        metadata.update(values)
        write_json_atomic(path, metadata)