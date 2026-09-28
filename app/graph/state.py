from typing import Annotated, TypedDict, Literal
import operator
from langchain_core.messages import BaseMessage
from langchain_core.documents import Document


class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]

    # The YouTube video ID — set at the start of each request
    video_id: str

    decision_route: Literal["chat", "rag", "summary", "web_search"]

    video_title: str

    detailed_summary: bool

    video_summary: str

    summary_error: str

    summary_answer: str

    documents: list[Document]

    grade: dict

    web_search_needed: bool

    web_search_error: str

    web_results: list[dict]

    context: str

    rewritten_query: str

    retry_count: int

    # Fast-path flag: skip the grader when retrieval looks sufficient
    skip_grader: bool

    # Actual source state used for the answer.
    source_type: Literal["rag", "web", "rag_web"]