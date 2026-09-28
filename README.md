# YouTube RAG Chatbot

A production-quality Chrome Extension (Manifest V3) and FastAPI backend that allows users to chat with the YouTube video they are currently watching using Retrieval-Augmented Generation (RAG).

## Overview

The project consists of two main parts:
- **Backend (`/app`)**: A Python FastAPI application that uses LangChain, LangGraph, and FAISS to download YouTube transcripts, chunk and embed them, and answer user queries using RAG.
- **Frontend (`/frontend`)**: A Chrome Extension that detects the active YouTube video, displays a conversational UI, and interacts with the backend.

## Features

- **Automatic Video Detection**: Detects the active YouTube video's URL and title automatically from the extension.
- **Conversational Memory**: Remembers context from previous questions within a session.
- **RAG Architecture**: Leverages LangGraph for dynamic routing, FAISS for vector storage, and HuggingFace/Groq/Gemini for LLM processing.
- **Web Search Fallback**: Automatically falls back to web search or direct generation if context is not found in the video transcript.

## Folder Structure

```
youtube-rag-extension/
├── app/                 # FastAPI Backend & LangGraph workflows
│   ├── graph/           # LangGraph nodes, edges, and state
│   ├── ingestion/       # Transcript processing & chunking
│   ├── llm/             # LLM integrations
│   └── main.py          # FastAPI application entrypoint
├── frontend/            # Chrome Extension
│   ├── background/      # Service worker for API requests
│   ├── popup/           # Extension UI and logic
│   ├── content/         # Content scripts (optional)
│   └── manifest.json    # Extension manifest
├── requirements.txt     # Backend dependencies
└── README.md            # This file
```

## Getting Started

### 1. Backend Setup

1. Make sure you have Python 3.10+ installed.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(Alternatively, use `uv` if you prefer it for dependency management as `uv.lock` is present in `app/`)*

3. Create a `.env` file in the root directory (or in `app/`) and add your API keys:
   ```env
   GROQ_API_KEY=your_groq_api_key
   GOOGLE_API_KEY=your_gemini_api_key
   TAVILY_API_KEY=your_tavily_api_key  # Optional, for web search
   ```

4. Start the FastAPI server:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   The backend will be available at `http://localhost:8000`.

### 2. Extension Installation

1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer Mode** using the toggle in the top right corner.
3. Click the **Load unpacked** button.
4. Select the `frontend` folder from this repository.
5. The extension will appear in your Chrome toolbar. Click the extension icon while watching a YouTube video to start chatting!

## Architecture Details

When a user asks a question via the Chrome extension:
1. The extension sends a request with the video URL and question to the backend.
2. The FastAPI backend extracts the Video ID and fetches its transcript via `youtube-transcript-api`.
3. The transcript is processed and stored in a FAISS vector database.
4. A **LangGraph workflow** evaluates the query, retrieves relevant chunks from FAISS, and calls the LLM (e.g. Groq/Gemini) to generate an answer.
5. The answer is streamed back to the extension UI.
