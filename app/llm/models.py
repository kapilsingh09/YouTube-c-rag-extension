import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env relative to this file so it works regardless of CWD
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from langchain_groq import ChatGroq
<<<<<<< HEAD

GROQ_API_KEY_1 = os.getenv("GROQ_API_KEY_1")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2")

# Primary Groq model set
groq_llm_1 = ChatGroq(
    model='openai/gpt-oss-20b',
    temperature=0.2,
    max_retries=2,
    api_key=GROQ_API_KEY_1,
)

groq_llm_2 = ChatGroq(
    model='qwen/qwen3.8-27b',
    temperature=0.2,
    max_retries=2,
    api_key=GROQ_API_KEY_2,
)

# Backward-compatible alias used by older imports.
groq_llm = groq_llm_1

# Legacy compatibility alias; kept only so older code paths do not crash.
# Gemini was removed from this graph, so all generation now rotates across Groq models.
google_llm = groq_llm_1

GROQ_MODEL_VARIANTS = (groq_llm_1, groq_llm_2)


def get_groq_variant(context_key: str | None = None):
    """Rotate between the two Groq models deterministically for a session/request."""
    source = context_key or "default"
    variant_index = abs(hash(source)) % len(GROQ_MODEL_VARIANTS)
    return GROQ_MODEL_VARIANTS[variant_index]


# print("Groq model variants initialized successfully.")
=======
from langchain_google_genai import ChatGoogleGenerativeAI

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# GOOGLE_MODEL = os.getenv("GOOGLE_LLM_MODEL", "gemini-3.8-flash")
# GROQ_MODEL = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-20b")

# Main LLM — Google Gemini
google_llm = ChatGoogleGenerativeAI(
    model='gemini-3.8-flash',
    api_key=GOOGLE_API_KEY,
)

# Secondary LLM — Groq (fast router / rewriter)
groq_llm = ChatGroq(
    model='openai/gpt-oss-20b',
    temperature=0.2,
    max_retries=2,
    api_key=GROQ_API_KEY,
)

# print("Google LLM and Groq LLM initialized successfully.")
>>>>>>> 13da7b824cf1679b856c26d8213f656a276d558e
