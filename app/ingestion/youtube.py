import logging
import time

from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled
from youtube_transcript_api import FetchedTranscript, FetchedTranscriptSnippet

from app.cache import (
    CACHE_SCHEMA_VERSION,
    read_json,
    update_video_metadata,
    video_cache_dir,
    video_processing_lock,
    write_json_atomic,
)


logger = logging.getLogger(__name__)


def _cached_transcript(video_id: str) -> FetchedTranscript | None:
    cache_path = video_cache_dir(video_id) / "transcript.json"
    payload = read_json(cache_path)
    if not payload or payload.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return None

    try:
        snippets = [FetchedTranscriptSnippet(**snippet) for snippet in payload["snippets"]]
        return FetchedTranscript(
            snippets=snippets,
            video_id=video_id,
            language=payload["language"],
            language_code=payload["language_code"],
            is_generated=payload["is_generated"],
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("[Cache] Invalid transcript cache; refetching: %s", video_id)
        return None


def _store_transcript(video_id: str, transcript: FetchedTranscript) -> None:
    snippets = [
        {"text": snippet.text, "start": snippet.start, "duration": snippet.duration}
        for snippet in transcript.snippets
    ]
    duration = max(
        (snippet["start"] + snippet["duration"] for snippet in snippets),
        default=0.0,
    )
    write_json_atomic(
        video_cache_dir(video_id) / "transcript.json",
        {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "video_id": video_id,
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "duration": duration,
            "snippets": snippets,
        },
    )
    update_video_metadata(
        video_id,
        {
            "transcript_available": True,
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "duration": duration,
        },
    )


def fetch_transcript(video_id: str):
    """
    Priority:
    1. English manual transcript
    2. English generated transcript
    3. Hindi transcript
    4. Any other available transcript
    """

    if not video_id:
        raise ValueError("A video_id is required to fetch a YouTube transcript.")

    with video_processing_lock(video_id):
        cached = _cached_transcript(video_id)
        if cached is not None:
            print(f"[Cache] Transcript HIT: {video_id}")
            return cached

        print(f"[Cache] Transcript MISS: {video_id}")
        started_at = time.perf_counter()

        try:
            ytt_api = YouTubeTranscriptApi()
            transcripts = list(ytt_api.list(video_id))

            if not transcripts:
                raise RuntimeError(
                    f"No transcript tracks are available for video '{video_id}'."
                )

            # 1. English manual transcript
            transcript = next(
                (
                    t for t in transcripts
                    if t.language_code.lower().startswith("en")
                    and not t.is_generated
                ),
                None
            )

            # 2. English generated transcript
            if transcript is None:
                transcript = next(
                    (
                        t for t in transcripts
                        if t.language_code.lower().startswith("en")
                    ),
                    None
                )

            # 3. Hindi transcript
            if transcript is None:
                transcript = next(
                    (
                        t for t in transcripts
                        if t.language_code.lower().startswith("hi")
                    ),
                    None
                )

            # 4. Any other available language
            if transcript is None:
                transcript = transcripts[0]

            print(
                f"[Transcript] Using {transcript.language_code} "
                f"(generated={transcript.is_generated}) for {video_id}"
            )

            fetched = transcript.fetch()
            try:
                _store_transcript(video_id, fetched)
            except OSError:
                logger.exception("[Cache] Could not persist transcript: %s", video_id)
            elapsed = time.perf_counter() - started_at
            print(f"[Cache] Transcript stored: {video_id} ({elapsed:.2f}s)")
            return fetched

        except TranscriptsDisabled as e:
            raise RuntimeError(
                "Transcripts are disabled for this video, so I can't access its transcript. "
                "Please try another YouTube video with captions enabled."
            ) from e

        except Exception as e:
            raise RuntimeError(
                f"Failed to fetch transcript for video '{video_id}': {e}"
            ) from e


# code behaviour 

# Available transcripts
#         │
#         ├── English manual? ──→ YES → USE IT
#         │
#         ├── English generated? → YES → USE IT
#         │
#         ├── Hindi? ────────────→ YES → USE IT
#         │
#         └── Anything else? ───→ YES → USE IT