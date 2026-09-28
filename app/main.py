import asyncio
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from app.cache import prepare_video_cache

load_dotenv()

from app.graph.workflow import graph as chatbot


app = FastAPI(title="YouTube RAG Chatbot API")


# ──────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Request schema
# ──────────────────────────────────────────────

class AskRequest(BaseModel):
    youtube_url: str
    video_title: str | None = None
    question: str
    session_id: str
    model: Literal["gemini", "groq"] = "groq"
    api_key: str | None = None


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def extract_video_id(youtube_url: str) -> str:
    """Extract the YouTube video ID from a watch URL."""

    parsed = urlparse(youtube_url)
    params = parse_qs(parsed.query)

    video_ids = params.get("v", [])

    if not video_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract video ID from URL: {youtube_url}"
        )

    return video_ids[0]


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.get("/")
async def read_root():
    return {
        "message": "YouTube RAG Chatbot API is running."
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time()
    }


@app.post("/ask")
async def ask(request: AskRequest):
    """
    Main chat endpoint.

    Chrome extension sends:

    {
        "youtube_url": "https://www.youtube.com/watch?v=...",
        "question": "...",
        "session_id": "sess_...",
        "model": "groq",
        "api_key": ""
    }

    The video ID is extracted and the LangGraph workflow
    is executed. The final answer is streamed as plain text.
    """

    # ──────────────────────────────────────────
    # Extract YouTube video ID
    # ──────────────────────────────────────────

    video_id = extract_video_id(request.youtube_url)
    video_title = await asyncio.to_thread(
        prepare_video_cache,
        video_id,
        request.video_title,
    )


    # ──────────────────────────────────────────
    # LangGraph config
    # ──────────────────────────────────────────

    config = {
        "configurable": {
            "thread_id": request.session_id,
            "model": request.model,
        }
    }


    # ──────────────────────────────────────────
    # Initial LangGraph state
    # ──────────────────────────────────────────

    initial_state = {
        "messages": [
            HumanMessage(content=request.question)
        ],
        "video_id": video_id,
        "video_title": video_title,
    }


    # ──────────────────────────────────────────
    # Generate response
    # ──────────────────────────────────────────

    async def generate():
        try:
            start_time = time.perf_counter()
            first_token_received = False
            answer_streamed = False
            streamed_answer = ""

            # Stream LLM tokens and the non-streamed stored-summary response.
            async for mode, event in chatbot.astream(
                initial_state,
                config=config,
                stream_mode=["messages", "updates"]
            ):
                if mode == "updates":
                    if "web_search" in event:
                        yield "\u001eWEB_SEARCH_USED\u001e"
                    if "retriever" in event:
                        yield "\u001eRAG_USED\u001e"

                    summary_update = event.get("summary_response", {})
                    summary_answer = summary_update.get("summary_answer")
                    if summary_answer:
                        yield summary_answer
                    if not answer_streamed:
                        for node_name in ("generate", "chat", "detailed_summary"):
                            node_update = event.get(node_name, {})
                            messages = node_update.get("messages", [])
                            if messages:
                                answer = messages[-1].content
                                if isinstance(answer, str) and answer:
                                    yield answer
                                    answer_streamed = True
                                    break
                    continue

                chunk, metadata = event

                # Check for if this chunk is from the final generation node (or chat node)
                # and contains actual content
                if metadata.get("langgraph_node") in ["generate", "chat", "detailed_summary"]:
                    content = chunk.content
                    if isinstance(content, str):
                        content_text = content
                    elif isinstance(content, list):
                        content_text = "".join(
                            item.get("text", "") if isinstance(item, dict) else item
                            for item in content
                            if isinstance(item, (str, dict))
                        )
                    else:
                        content_text = ""

                    if content_text:
                        if streamed_answer and content_text.startswith(streamed_answer):
                            content_text = content_text[len(streamed_answer):]

                    if content_text:
                        if not first_token_received:
                            first_token_received = True
                            elapsed = time.perf_counter() - start_time
                            print(
                                f"[Timing] End-to-end first answer token after "
                                f"{elapsed:.2f}s (includes routing, retrieval, grading, "
                                "search, and generation)"
                            )

                        answer_streamed = True
                        streamed_answer += content_text
                        yield content_text

        except Exception as e:
            yield f"\n\n[ERROR]: {str(e)}"


    # ──────────────────────────────────────────
    # Return streaming response
    # ──────────────────────────────────────────

    return StreamingResponse(
        generate(),
        media_type="text/plain"
    )


#server entrypoint

if __name__ == "__main__":

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parents[1])],
    )
