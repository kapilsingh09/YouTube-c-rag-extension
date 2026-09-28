from pydantic import BaseModel, Field
from typing import Literal
import logging
import os
import re
import time

from app.rag.retriever import get_retriever
from app.ingestion.video_summary import ensure_video_summary
from app.llm.models import groq_llm, get_groq_variant
from app.graph.state import GraphState
from tavily import TavilyClient
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, SystemMessage


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
logger = logging.getLogger(__name__)

def normalize_text(value):
    """Convert model outputs into a plain string regardless of whether the SDK returns a string, list, or dict."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = normalize_text(item)
            if text:
                parts.append(text)
        return " ".join(parts).strip()
    if isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                return normalize_text(value[key])
        return str(value).strip()
    return str(value).strip()


def stream_text(value):
    """Extract streamed text without stripping token-boundary whitespace."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(stream_text(item) for item in value)
    if isinstance(value, dict):
        if "text" in value:
            return stream_text(value["text"])
        if "content" in value:
            return stream_text(value["content"])
    return str(value)


# ──────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────

class RouteDecision(BaseModel):
    decision_route: Literal["chat", "rag", "summary", "web_search"]
    detailed_summary: bool = Field(
        default=False,
        description="True only when a whole-video summary requests a specific focus, examples, timestamps, evidence, or extra detail.",
    )

router_llm = groq_llm.with_structured_output(RouteDecision)


def router_model_for_state(state: GraphState):
    return get_groq_variant(state.get("video_id") or state["messages"][-1].content)


def is_casual_message(message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s']", "", message.lower()).strip()
    casual_messages = {
        "hi", "hii", "hello", "helo", "hallo", "hey", "heyy", "hello there", "hi there",
        "good morning", "good afternoon", "good evening", "how are you", "how are you doing",
        "thanks", "thank you", "thank you so much", "ok", "okay", "bye", "goodbye",
        "what can you do",
    }
    return normalized in casual_messages


def is_topic_summary_request(message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9\s]", " ", message.lower())
    summary_match = re.search(r"\b(?:summari[sz]e|summary|sum up)\b", normalized)
    if not summary_match:
        return False

    whole_video_request = re.search(
        r"\b(?:whole|entire|full|overall)\s+video\b"
        r"|\b(?:summari[sz]e|summary|sum up)\s+(?:the|this)\s+video\b"
        r"|\b(?:main points|overview)\b",
        normalized,
    )
    if whole_video_request:
        return False

    filler_words = {
        "a", "about", "can", "do", "for", "from", "give", "i", "in", "me",
        "of", "on", "please", "the", "this", "that", "these", "those", "to",
        "video", "want", "what", "with", "you", "your", "bro", "things", "thing",
    }
    words = normalized.split()
    summary_index = len(normalized[:summary_match.start()].split())
    topic_words = [
        word
        for word in words[:summary_index] + words[summary_index + len(summary_match.group().split()):]
        if word not in filler_words
        and not re.fullmatch(r"summari[sz]e|summary|sum|up", word)
    ]
    return bool(topic_words)


def make_decision_route(state: GraphState):
    logger.info("Graph node triggered: router")
    user_message = state["messages"][-1].content

    if isinstance(user_message, str) and is_casual_message(user_message):
        return {"decision_route": "chat", "detailed_summary": False}
    if isinstance(user_message, str) and is_topic_summary_request(user_message):
        return {"decision_route": "rag", "detailed_summary": False}

    prompt = f"""
You are a routing agent for a YouTube RAG chatbot.

Your job is to decide whether the user's message should be handled as normal conversation or answered using information from the YouTube video/transcript.

Choose exactly one route:

chat
rag
summary
web_search

### Use "chat" when:

The user is having normal conversation or casual interaction that does not require information from the video.

Examples:

* "Hi"
* "Hello"
* "How are you?"
* "Thanks"
* "Okay"
* "Bye"
* "What can you do?"

### Use "rag" when:

The user asks anything that is related to, based on, or could be answered using information from the YouTube video/transcript.

This includes:

* Asking about a concept or topic explained in the video
* Asking for an explanation of something discussed in the video
* Asking what the speaker said or explained
* Asking for a summary of a specific part of the video
* Asking about examples mentioned in the video
* Asking why or how something works when it is discussed in the video
* Asking about specific people, technologies, ideas, terms, or topics mentioned in the video
* Asking follow-up questions about something previously discussed from the video
* Asking questions that require understanding the video's content

### Use "summary" when:

The user wants a whole-video overview, summary, main points, or explanation of the entire video.
Examples include "Summarize the video", "Give me an overview", "What are the main points?", and "What is this video about?".
If the user asks to summarize a named topic or concept, use "rag" to summarize only the relevant transcript content. Do not treat the topic name as a request for a whole-video summary.

Set detailed_summary to true only if the whole-video summary also requests a specific focus, examples, evidence, timestamps, or extra detail. A simple summary request must set it to false.

### Use "web_search" when:

The user asks only for web research or current/external information, without asking what the video says.

If the user asks for both information from the video and current/external verification,
choose "rag" first. The grader will use Web Search for information missing from the transcript,
so both sources can contribute to the answer.

Examples:
"Use Tavily to search the web for this."
"Internet par latest information search karo."
→ web_search

"What does this video say about SSI, and what is SSI currently doing?"
→ rag

Examples:
"What is self-supervised learning?"
→ rag

"Explain what the video says about LLMs."
→ rag

"Can you summarize this video?"
→ summary

"What did the speaker say about transformers?"
→ rag

"Search the web for the latest LangGraph release."
→ web_search

### Important:

If the user asks about a specific fact or topic from the video, choose "rag".

When you are unsure whether the question is casual conversation or requires video information, choose "rag".

Do not answer the user's question. Only classify it.
Return only a valid JSON object with keys "decision_route" and "detailed_summary".
Use a string for "decision_route" and a boolean for "detailed_summary".

User message:
{user_message}

"""

    try:
        model = router_model_for_state(state)
        response = model.with_structured_output(
            RouteDecision,
            method="json_mode",
        ).invoke(prompt)
        return {
            "decision_route": response.decision_route,
            "detailed_summary": response.detailed_summary,
        }
    except Exception:
        logger.exception("Router classification failed; using the RAG route")
        return {"decision_route": "rag", "detailed_summary": False}


# ──────────────────────────────────────────────
# CHAT
# ──────────────────────────────────────────────

from langchain_core.runnables import RunnableConfig

def response_llm(config: RunnableConfig):
    provider = (config.get("configurable") or {}).get("model", "groq")
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "default")
    variant = get_groq_variant(thread_id)
    return variant if provider in {"groq", "gemini"} else variant


def chat_node(state: GraphState, config: RunnableConfig):
    logger.info("Graph node triggered: chat")

    user_text = state["messages"][-1].content if state["messages"] else ""
    normalized = normalize_text(user_text).lower()
    short_greetings = {
        "hi",
        "hello",
        "hey",
        "hii",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
    }

    if normalized in short_greetings:
        return {
            "messages": [AIMessage(content="Hi! How can I help you?")]
        }

    response = response_llm(config).invoke(
        [
            SystemMessage(content="Reply in the same language as the user's latest message. If the user is speaking in English, reply in English. If they switch to Hindi or any other language, reply in that language instead. For short greetings such as hi/hello, keep it brief and polite in English."),
            *state["messages"],
        ],
        config=config,
    )

    return {
        "messages": [response]
    }


# ──────────────────────────────────────────────
# GRADER
# ──────────────────────────────────────────────

class ContextGrade(BaseModel):

    relevance: int = Field(
        description="How relevant the retrieved context is to the question. Score 0-10."
    )

    completeness: int = Field(
        description="How completely the context answers the question. Score 0-10."
    )

    specificity: int = Field(
        description="How specifically the context addresses the exact question. Score 0-10."
    )

    confidence: int = Field(
        description="How confident we can be that the context supports a correct answer. Score 0-10."
    )

    action: Literal[
        "generate",
        "rewrite_query",
        "web_search"
    ] = Field(
        description="Corrective action to take based on the retrieved context."
    )

    reason: str = Field(
        description="Short explanation for the grading decision."
    )



def grader_model_for_state(state: GraphState):
    return get_groq_variant(state.get("video_id") or state["messages"][-1].content)


def grade_documents(state: GraphState):
    logger.info("Graph node triggered: grader")

    question = state["messages"][-1].content
    video_title = state.get("video_title", "").strip()

    documents = state.get("documents", [])

    context = "\n\n".join(
        doc.page_content
        for doc in documents
    )
    if video_title:
        context = f"Video title: {video_title}\n\n{context}"

    prompt = f"""
You are the corrective grading agent in a YouTube C-RAG system.

Your job is to evaluate whether the retrieved YouTube transcript
is good enough to answer the user's question.

Score:

1. relevance: 0-10
2. completeness: 0-10
3. specificity: 0-10
4. confidence: 0-10

Then choose exactly ONE action:

generate
rewrite_query
web_search


Use "generate" when:
- The transcript is relevant.
- The transcript contains enough information.
- The answer can be safely generated from the transcript.

Use "rewrite_query" when:
- The retrieved documents are poorly relevant.
- The retrieval query appears to have missed the correct part of the transcript.
- A better query could reasonably retrieve better information from the YouTube transcript.

Use "web_search" when:
- The transcript does not contain the required information.
- The user explicitly asks for internet/web research.
- The question requires current/latest information.
- The required information is outside the video's scope.

Important:
Do NOT choose rewrite_query just because the answer is not in the transcript.
If the information genuinely does not exist in the transcript,
choose web_search.

Return only a valid JSON object with keys "relevance", "completeness",
"specificity", "confidence", "action", and "reason". The first four
values must be integers from 0 to 10. "action" must be "generate",
"rewrite_query", or "web_search". "reason" must be a short string.

User Question:
{question}

Retrieved YouTube Context:
{context}
"""

    grader_started = time.perf_counter()
    model = grader_model_for_state(state)
    result = model.with_structured_output(
        ContextGrade,
        method="json_mode",
    ).invoke(prompt)
    print(f"[Timing] Grader completed in {time.perf_counter() - grader_started:.2f}s")

    overall_score = (
        result.relevance
        + result.completeness
        + result.specificity
        + result.confidence
    ) / 4

    grade = {
        "relevance": result.relevance,
        "completeness": result.completeness,
        "specificity": result.specificity,
        "confidence": result.confidence,
        "overall_score": overall_score,
        "action": result.action,
        "reason": result.reason
    }

    return {
        "grade": grade,
        "web_search_needed": result.action == "web_search"
    }


# ──────────────────────────────────────────────
# REWRITE QUERY
# ──────────────────────────────────────────────

rewrite_prompt = """
You are a query rewriting agent for a YouTube RAG system.

Rewrite the user's question into a concise search query
that is more likely to retrieve the correct information
from the YouTube transcript.

Do not answer the question.

Return only the rewritten search query.

Original question:
{question}

Previous retrieved context:
{context}
"""


def rewrite_query_node(state: GraphState):
    logger.info("Graph node triggered: rewrite_query")

    question = state["messages"][-1].content

    documents = state.get("documents", [])

    context = "\n\n".join(
        doc.page_content
        for doc in documents
    )

    prompt = rewrite_prompt.format(
        question=question,
        context=context
    )

    model = get_groq_variant(state.get("video_id") or state["messages"][-1].content)
    response = model.invoke(prompt)

    rewritten_query = normalize_text(response.content)

    return {
        "rewritten_query": rewritten_query,
        "retry_count": state.get("retry_count", 0) + 1
    }


# ──────────────────────────────────────────────
# RETRIEVE  (uses video_id from state)
# ──────────────────────────────────────────────

def retrieve_node(state: GraphState):
    logger.info("Graph node triggered: retriever")

    video_id = state.get("video_id", "")

    if state.get("rewritten_query"):
        query = state["rewritten_query"]
    else:
        query = state["messages"][-1].content
        
        # Optimize complex or hybrid queries for FAISS semantic search
        if len(query.split()) > 8:
            try:
                optimization_prompt = f"""You are a search query optimizer.
Extract ONLY the core topic, entities, and keywords from the user's question for a vector database search against a video transcript.
Ignore conversational filler (e.g., "According to this video", "what does it say").
Ignore questions about time or external context (e.g., "what has changed since publication", "search the web").
Return ONLY the essential keywords separated by spaces.

Question: {query}
Keywords:"""
                model = get_groq_variant(video_id)
                response = model.invoke(optimization_prompt)
                from app.graph.nodes import normalize_text
                optimized = normalize_text(response.content)
                if optimized and len(optimized) > 2:
                    print(f"[Graph] Original query: {query}")
                    print(f"[Graph] Optimized FAISS query: {optimized}")
                    query = optimized
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Query optimization failed: {e}")

    t0 = time.time()
    retriever = get_retriever(video_id)
    documents = retriever.invoke(query)
    elapsed = time.time() - t0

    print(f"[Graph] Retrieval completed in {elapsed:.2f}s — {len(documents)} docs returned")

    return {
        "documents": documents
    }


def video_summary_node(state: GraphState):
    logger.info("Graph node triggered: video_summary")
    try:
        summary = ensure_video_summary(state.get("video_id", ""))
        return {"video_summary": summary, "summary_error": ""}
    except Exception as error:
        logger.exception("Could not retrieve or generate the video summary")
        return {"video_summary": "", "summary_error": str(error)}


def summary_response_node(state: GraphState):
    summary = state.get("video_summary", "")
    if summary:
        answer = summary
    else:
        answer = (
            "I couldn't access a summary for this video. "
            "Check that the video has an available transcript and try again."
        )

    return {
        "messages": [AIMessage(content=answer)],
        "summary_answer": answer,
    }


def detailed_summary_node(state: GraphState, config: RunnableConfig):
    logger.info("Graph node triggered: detailed_summary")
    question = state["messages"][-1].content
    summary = state.get("video_summary", "")

    try:
        documents = get_retriever(state.get("video_id", "")).invoke(question)
    except Exception:
        logger.exception("Detailed-summary retrieval failed; using stored summary only")
        documents = []

    excerpts = "\n\n".join(doc.page_content for doc in documents)
    context = (
        f"Stored video summary:\n{summary}\n\n"
        f"Relevant transcript excerpts:\n{excerpts}"
    )
    chain = generate_prompt | response_llm(config)
    response_parts = []
    for chunk in chain.stream({"context": context, "question": question}, config=config):
        content = stream_text(chunk.content)
        if content:
            response_parts.append(content)

    return {"messages": [AIMessage(content="".join(response_parts))]}


# ──────────────────────────────────────────────
# SHOULD GRADE  (fast-path decision node)
# ──────────────────────────────────────────────

def should_grade_node(state: GraphState):
    logger.info("Graph node triggered: should_grade")
    documents = state.get("documents", [])
    retry_count = state.get("retry_count", 0)

    print(f"[Graph] Running grader: {len(documents)} docs, retry={retry_count}")
    return {"skip_grader": False}


# ──────────────────────────────────────────────
# WEB SEARCH
# ──────────────────────────────────────────────

@tool
def tavily_search(query: str):
    """
    Search the internet for additional information
    when the YouTube transcript does not contain enough information.
    """

    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured in the backend environment.")

    response = tavily_client.search(
        query=query,
        search_depth="basic",
        max_results=4
    )

    if not isinstance(response, dict):
        return []
    results = response.get("results") or []
    return [result for result in results if isinstance(result, dict)]


def web_search_node(state: GraphState):
    logger.info("Graph node triggered: web_search")

    question = state["messages"][-1].content
    video_title = state.get("video_title", "")
    
    # Inject context to prevent hilarious hallucinations (e.g., Jev = Japanese Encephalitis Vaccine)
    search_query = f"{video_title} {question}" if video_title else question
    
    print(f"[Web] Internet search via Tavily: START (query: '{search_query}')")

    search_started = time.perf_counter()
    try:
        results = tavily_search.invoke({"query": search_query}) or []
        error = ""
        if not results:
            logger.warning("Tavily returned no results for the request")
            print("[Web] Tavily search completed: 0 results")
        else:
            print(f"[Web] Tavily search completed: {len(results)} results")
    except Exception as exception:
        logger.exception("Tavily web search failed")
        print(f"[Web] Tavily search failed: {type(exception).__name__}")
        results = []
        error = str(exception)

    print(f"[Timing] Tavily completed in {time.perf_counter() - search_started:.2f}s")

    return {
        "web_results": results,
        "web_search_error": error,
    }


# ──────────────────────────────────────────────
# CONTEXT FUSION
# ──────────────────────────────────────────────

def context_fusion_node(state: GraphState):
    logger.info("Graph node triggered: context_fusion")

    youtube_context = "\n\n".join(
        doc.page_content
        for doc in state.get("documents", [])
    )

    web_context = "\n\n".join(
        f"""
Title: {result.get("title") or ""}
Source: {result.get("url") or ""}
Content:
{result.get("content") or ""}
"""
        for result in state.get("web_results", [])
        if isinstance(result, dict)
    )
    if not web_context:
        if state.get("web_search_error"):
            web_context = f"Web search failed: {state['web_search_error']}"
        else:
            web_context = "Tavily returned no web results for this query."

    final_context = f"""
===== YOUTUBE TRANSCRIPT =====

{youtube_context}

===== WEB SEARCH RESULTS =====

{web_context}
"""

    return {
        "context": final_context
    }


# ──────────────────────────────────────────────
# GENERATE
# ──────────────────────────────────────────────

generate_prompt = ChatPromptTemplate.from_template("""
You are a factual QA agent for a YouTube RAG system.
You have no callable tools. Never attempt a tool call or another web search.
Web Search, if needed, has already been performed; use only the source material below.

Your primary source is the retrieved YouTube transcript/context.

## SOURCE PRIORITY
Use the YouTube RAG context as the DEFAULT and PRIMARY source.
Prefer RAG whenever the user's question can be answered from the retrieved transcript.
Use Web Search only when necessary.
The system should be RAG-heavy, not Web-heavy.

## ROUTING RULES
Choose exactly one route:

### 1. RAG
Use RAG when:
- The answer is explicitly present in the retrieved transcript.
- The question asks about something discussed in the video.
- The question can be answered using only the video context.
- The question asks for an explanation, fact, example, person, event, or concept that is sufficiently covered by the transcript.
Do NOT use Web just because Web could provide additional information.
If RAG contains enough information, answer ONLY from RAG.

### 2. WEB
Use Web when:
- The requested information is not available in the video/RAG context.
- The question explicitly asks for current/latest/recent information.
- The question asks about information that exists outside the video.
- The question asks about current prices, current positions, current events, recent research, recent statements, current statistics, etc.
- The question is completely unrelated to the video and requires external knowledge.
Examples:
"What is Geoffrey Hinton's current research position?"
"What is Nvidia's current stock price?"
"What has Geoffrey Hinton said about AI safety recently?"
These should use Web, not RAG-only.

### 3. BOTH
Use BOTH only when the question genuinely requires information from both sources.
For example:
"What did the video say about ImageNet, and what is the current state of ImageNet today?"
The first part requires RAG.
The second part requires Web.
Another example:
"According to the video, what was Hinton's contribution to AlexNet, and how did that contribution influence modern AI?"
Use RAG for Hinton's contribution described in the video and Web for external/current context if the video does not contain it.
Do NOT choose BOTH unnecessarily.
If RAG alone is sufficient, use RAG.

## IMPORTANT ANTI-HALLUCINATION RULE
Never invent information that is not supported by the available source.
If RAG is selected and the retrieved context does not contain enough information to answer the question:
DO NOT guess.
DO NOT use general model knowledge as if it came from the video.
Instead, return:
"📚 RAG: The available video context does not contain enough information to answer this."
If relevant Web Search results are included below, answer the external part using those results.
If no relevant Web Search results are included, clearly state that the information could not be verified.

## SOURCE SEPARATION
Never mix RAG and Web information without identifying their sources.
When using RAG information, label the relevant answer:
**📚 From the video (RAG):**
...
When using Web information, label it:
**🌐 From the web:**
...
When both are used:
**📚 From the video (RAG):**
...

**🌐 From the web:**
...
Keep the source sections clearly separated.
Do not present Web information as if it was stated in the video.
Do not present information from the video as if it came from Web Search.

## CURRENT INFORMATION
Words such as:
- current
- currently
- latest
- recent
- today
- now
- as of 2026
- recently
- latest research
- current position
- current price
usually indicate that Web Search is required.
However, if the video itself explicitly contains the requested current information and the question does not require verification beyond the video's content, RAG may be used.

## OUT-OF-SCOPE QUESTIONS
If the question is unrelated to the video:
Prefer Web Search rather than forcing the question through RAG.
Example:
User: "What is the current population of the United States?"
Do NOT answer:
"According to the video..."
Instead use Web Search.

## PARTIAL ANSWERS
If a question has two parts and RAG can answer only one part:
Do NOT stop after the RAG answer.
Determine whether the missing part requires Web Search.
Example:
User: "What is ImageNet, and who currently maintains it?"
If RAG contains the definition of ImageNet but not its current maintainer:
Use BOTH.
Return:
**📚 From the video (RAG):**
ImageNet is ...

**🌐 From the web:**
The current information about its maintenance is ...

## FINAL ANSWER STYLE
Keep answers concise and factual.
Do not mention internal routing decisions such as:
- "The router selected RAG."
- "The router selected BOTH."
- "I classified this as Web."
- "The retrieval score was..."
Only show the source labels to the user.
Use citations when available.

## SOURCE INTEGRITY
Every factual claim must be supported by the source used for that claim.
Never fabricate citations.
Never fabricate transcript information.
Never assume that information missing from RAG is true simply because the model knows it.
When uncertain, say that the available evidence is insufficient.

## PRIORITY ORDER
Follow this decision order:
1. Can RAG answer the question completely?
→ Use RAG.
2. Can RAG answer only part of the question?
→ Use BOTH if Web can answer the missing part.
3. Is the information current/recent/external and absent from RAG?
→ Use WEB.
4. Is the question unrelated to the video?
→ Use WEB.
5. If neither RAG nor Web can provide reliable evidence:
→ Say that the information could not be verified.
The goal is:
RAG first → Web only when necessary → BOTH when genuinely required → never hallucinate.

Use only the provided context and web results to answer. Do not invent missing details.

Context:
{context}

Question:
{question}
""")


def generate_node(state: GraphState, config: RunnableConfig):
    logger.info("Graph node triggered: generate")

    question = state["messages"][-1].content

    # If context fusion already created context
    if state.get("context"):
        context = state["context"]

    else:
        # Use YouTube documents directly
        context = "\n\n".join(
            doc.page_content
            for doc in state.get("documents", [])
        )

    video_title = state.get("video_title", "").strip()
    if video_title:
        context = f"Video title: {video_title}\n\n{context}"

    provider = (config.get("configurable") or {}).get("model", "groq")
    generation_started = time.perf_counter()
    first_token_logged = False
    has_rag = bool(state.get("documents"))
    has_web = bool(state.get("web_results"))
    source_type = "rag_web" if has_rag and has_web else "rag" if has_rag else "web" if has_web else "rag"
    print(f"[Graph] Generation started (model={provider})")

    # Groq is used for the user-facing stream because it avoids Gemini's
    # exhausted free-tier generation quota and returns the first token faster.
    chain = generate_prompt | response_llm(config)

    # Use the native model stream so LangGraph's messages stream can forward
    # tokens to the API while the final message is still stored in state.
    response_parts = []
    for chunk in chain.stream({
        "context": context,
        "question": question
    }, config=config):
        content = stream_text(chunk.content)
        if content:
            if not first_token_logged:
                first_token_logged = True
                print(
                    f"[Timing] Generation first token in "
                    f"{time.perf_counter() - generation_started:.2f}s (model={provider})"
                )
            response_parts.append(content)

    print(f"[Timing] Generation completed in {time.perf_counter() - generation_started:.2f}s")

    response = AIMessage(content="".join(response_parts))

    return {
        "messages": [response]
    }


# ──────────────────────────────────────────────
# RESET STATE
# ──────────────────────────────────────────────

def reset_request_state(state: GraphState):
    logger.info("Graph node triggered: reset_request_state")
    return {
        "retry_count": 0,
        "rewritten_query": "",
        "context": "",
        "web_results": [],
        "web_search_error": "",
        "documents": [],
        "grade": {},
        "skip_grader": False,
        "detailed_summary": False,
        "video_summary": "",
        "summary_error": "",
        "summary_answer": "",
    }
