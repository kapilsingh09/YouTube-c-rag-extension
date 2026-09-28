import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.vectorstores import VectorStoreRetriever

from app.cache import (
    CACHE_SCHEMA_VERSION,
    read_json,
    update_video_metadata,
    video_cache_dir,
    video_processing_lock,
    write_json_atomic,
)
from app.rag.vector_store import create_vector_store
from app.rag.embedding import embedding_model
from app.ingestion.transcript import (
    CHUNKING_VERSION,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    build_chunks,
)

# In-memory cache: video_id -> retriever
# Bound memory use while keeping recently active videos warm.
_retriever_cache: OrderedDict[tuple[str, str], VectorStoreRetriever] = OrderedDict()
_retriever_cache_lock = threading.Lock()
_MAX_CACHED_RETRIEVERS = max(1, int(os.getenv("VIDEO_RETRIEVER_CACHE_SIZE", "8")))
logger = logging.getLogger(__name__)


def _faiss_fingerprint() -> tuple[str, dict]:
    settings = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "chunking_version": CHUNKING_VERSION,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "embedding_model": getattr(
            embedding_model,
            "model_name",
            f"{type(embedding_model).__module__}.{type(embedding_model).__qualname__}",
        ),
    }
    serialized = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16], settings


def _memory_retriever(cache_key: tuple[str, str]) -> VectorStoreRetriever | None:
    with _retriever_cache_lock:
        retriever = _retriever_cache.get(cache_key)
        if retriever is not None:
            _retriever_cache.move_to_end(cache_key)
        return retriever


def _remember_retriever(
    cache_key: tuple[str, str],
    retriever: VectorStoreRetriever,
) -> None:
    with _retriever_cache_lock:
        _retriever_cache[cache_key] = retriever
        _retriever_cache.move_to_end(cache_key)
        while len(_retriever_cache) > _MAX_CACHED_RETRIEVERS:
            _retriever_cache.popitem(last=False)


def _load_faiss(video_id: str, fingerprint: str, settings: dict):
    index_path = video_cache_dir(video_id) / f"faiss-{fingerprint}"
    manifest = read_json(index_path / "manifest.json")
    if (
        not manifest
        or manifest.get("fingerprint") != fingerprint
        or manifest.get("settings") != settings
        or not (index_path / "index.faiss").is_file()
        or not (index_path / "index.pkl").is_file()
    ):
        return None

    try:
        vector_store = FAISS.load_local(
            str(index_path),
            embedding_model,
            allow_dangerous_deserialization=True,
        )
        print(f"[Cache] FAISS loaded from disk: {video_id}")
    except Exception:
        logger.exception("[Cache] FAISS disk cache invalid; rebuilding: %s", video_id)
        return None

    try:
        update_video_metadata(
            video_id,
            {
                "faiss_available": True,
                "faiss_fingerprint": fingerprint,
                "chunk_count": manifest.get("chunk_count"),
            },
        )
    except OSError:
        logger.exception("[Cache] Could not update FAISS metadata: %s", video_id)
    return vector_store


def get_retriever(video_id: str) -> VectorStoreRetriever:
    """
    Return a retriever for the given YouTube video_id.
    Builds the vector store on first call and caches it.
    """
    if not video_id:
        raise ValueError("A video_id is required to build a retriever.")

    fingerprint, settings = _faiss_fingerprint()
    cache_key = (video_id, fingerprint)
    retriever = _memory_retriever(cache_key)
    if retriever is not None:
        print(f"[Retriever] Cache HIT: {video_id}")
        print(f"[Cache] FAISS loaded from memory: {video_id}")
        return retriever

    # Another request may be building this video after the first memory lookup.
    with video_processing_lock(video_id):
        retriever = _memory_retriever(cache_key)
        if retriever is not None:
            print(f"[Retriever] Cache HIT: {video_id}")
            print(f"[Cache] FAISS loaded from memory: {video_id}")
            return retriever

        print(f"[Retriever] Cache MISS: {video_id}")
        started_at = time.perf_counter()
        vector_store = _load_faiss(video_id, fingerprint, settings)

        if vector_store is None:
            index_path = video_cache_dir(video_id) / f"faiss-{fingerprint}"
            chunks = build_chunks(video_id)
            vector_store = create_vector_store(chunks, embedding_model)
            try:
                vector_store.save_local(str(index_path))
                write_json_atomic(
                    index_path / "manifest.json",
                    {
                        "cache_schema_version": CACHE_SCHEMA_VERSION,
                        "fingerprint": fingerprint,
                        "settings": settings,
                        "chunk_count": len(chunks),
                    },
                )
                print(f"[Cache] FAISS saved to disk: {video_id}")
                update_video_metadata(
                    video_id,
                    {
                        "faiss_available": True,
                        "faiss_fingerprint": fingerprint,
                        "chunk_count": len(chunks),
                    },
                )
            except Exception:
                logger.exception("[Cache] Could not persist FAISS index: %s", video_id)

        retriever = vector_store.as_retriever(search_kwargs={"k": 5})
        _remember_retriever(cache_key, retriever)

        elapsed = time.perf_counter() - started_at
        print(f"[Retriever] Vector store ready in {elapsed:.2f}s")
        return retriever