from langgraph.graph import StateGraph, END, START
from .state import GraphState
from .nodes import (
    context_fusion_node,
    generate_node,
    reset_request_state,
    make_decision_route,
    grade_documents,
    retrieve_node,
    rewrite_query_node,
    chat_node,
    web_search_node,
    should_grade_node,
    video_summary_node,
    summary_response_node,
    detailed_summary_node,
)
from .edges import route_question, grade_route

g = StateGraph(GraphState)
g.add_node("reset_request_state", reset_request_state)
g.add_node("router", make_decision_route)
g.add_node("chat", chat_node)
g.add_node("video_summary", video_summary_node)
g.add_node("summary_response", summary_response_node)
g.add_node("detailed_summary", detailed_summary_node)

g.add_node("retriever", retrieve_node)
g.add_node("should_grade", should_grade_node)
g.add_node("grader", grade_documents)

g.add_node("rewrite_query", rewrite_query_node)

g.add_node("web_search", web_search_node)
g.add_node("context_fusion", context_fusion_node)

g.add_node("generate", generate_node)


#compile


g.add_edge(START, "reset_request_state")
g.add_edge("reset_request_state", "router")
g.add_conditional_edges(
    "router", route_question,
    {"chat": "chat",
    "rag": "retriever",
    "summary": "video_summary",
    "web_search": "web_search",
    }
)

#if the chat ->ended here  
g.add_edge("chat", END)

def route_summary(state: GraphState):
    if state.get("detailed_summary") and state.get("video_summary"):
        return "detailed_summary"
    return "summary_response"

g.add_conditional_edges(
    "video_summary",
    route_summary,
    {
        "summary_response": "summary_response",
        "detailed_summary": "detailed_summary",
    },
)
g.add_edge("summary_response", END)
g.add_edge("detailed_summary", END)

# After retrieval, decide if we need the grader or can fast-path to generate
g.add_edge("retriever", "should_grade")

def route_after_should_grade(state: GraphState):
    if state.get("skip_grader"):
        return "generate"
    return "grader"

g.add_conditional_edges(
    "should_grade",
    route_after_should_grade,
    {
        "generate": "generate",
        "grader": "grader"
    }
)

#if the retriever is called, we need to grade the documents and decide what to do next
g.add_conditional_edges(
    "grader",
    grade_route,
    {
        "generate": "generate",
        "rewrite_query": "rewrite_query",
        "web_search": "web_search"
    }
)

g.add_edge("rewrite_query", "retriever")
g.add_edge("web_search", "context_fusion")
g.add_edge("context_fusion", "generate")
g.add_edge("generate", END)



from langgraph.checkpoint.memory import InMemorySaver

memory = InMemorySaver()
graph = g.compile(
    checkpointer=memory
)
print("Graph compiled successfully.")
