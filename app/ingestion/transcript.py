import time

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.cache import (
    CACHE_SCHEMA_VERSION,
    read_json,
    update_video_metadata,
    video_cache_dir,
    video_processing_lock,
    write_json_atomic,
)
from app.ingestion.youtube import fetch_transcript


CHUNKING_VERSION = 1
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def build_chunks(video_id: str) -> list[Document]:
    if not video_id:
        raise ValueError("A video_id is required to build transcript chunks.")

    with video_processing_lock(video_id):
        cache_path = video_cache_dir(video_id) / f"chunks-v{CHUNKING_VERSION}.json"
        payload = read_json(cache_path)
        if (
            payload
            and payload.get("cache_schema_version") == CACHE_SCHEMA_VERSION
            and payload.get("chunking_version") == CHUNKING_VERSION
            and payload.get("video_id") == video_id
            and isinstance(payload.get("chunks"), list)
        ):
            print(f"[Cache] Chunks HIT: {video_id}")
            return [
                Document(
                    page_content=chunk["page_content"],
                    metadata=chunk["metadata"],
                )
                for chunk in payload["chunks"]
            ]

        print(f"[Cache] Chunks MISS: {video_id}")
        started_at = time.perf_counter()
        chunks = _build_chunks_uncached(video_id)
        try:
            write_json_atomic(
                cache_path,
                {
                    "cache_schema_version": CACHE_SCHEMA_VERSION,
                    "chunking_version": CHUNKING_VERSION,
                    "video_id": video_id,
                    "chunks": [
                        {"page_content": chunk.page_content, "metadata": chunk.metadata}
                        for chunk in chunks
                    ],
                },
            )
            update_video_metadata(video_id, {"chunk_count": len(chunks)})
        except OSError:
            print(f"[Cache] Could not persist chunks: {video_id}")
        elapsed = time.perf_counter() - started_at
        print(f"[Cache] Chunks stored: {video_id} ({elapsed:.2f}s, {len(chunks)} chunks)")
        return chunks


def _build_chunks_uncached(video_id: str) -> list[Document]:
    """
    Fetch the YouTube transcript for `video_id`, split it into chunks,
    and return a list of LangChain Documents with timestamp metadata.
    """
    transcript = fetch_transcript(video_id)

    # 1. Combine transcript snippets into one continuous text
    full_text = " ".join(snippet.text for snippet in transcript.snippets)

    # 2. Create a mapping from character position → timestamp
    char_timestamps = []
    current_position = 0

    for snippet in transcript.snippets:
        text = snippet.text
        start = snippet.start

        char_timestamps.append({
            "start_char": current_position,
            "end_char": current_position + len(text),
            "start_seconds": start
        })

        current_position += len(text) + 1  # +1 for the space

    # 3. Split the continuous transcript
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    text_chunks = splitter.create_documents([full_text])

    # 4. Attach timestamp metadata to every chunk
    chunks = []

    for chunk in text_chunks:
        chunk_start = full_text.find(chunk.page_content)

        # Find transcript snippet containing the beginning of this chunk
        timestamp = 0.0

        for item in char_timestamps:
            if item["start_char"] <= chunk_start < item["end_char"]:
                timestamp = item["start_seconds"]
                break

        chunks.append(
            Document(
                page_content=chunk.page_content,
                metadata={
                    "start": timestamp,
                    "source": video_id
                }
            )
        )

    return chunks