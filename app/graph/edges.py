from app.graph.state import GraphState

def route_question(state: GraphState):
    return state["decision_route"]

GRADE_THRESHOLD = 7
MAX_RETRIES = 2

def grade_route(state):
    grade = state["grade"]
    action = grade["action"]
    retry_count = state.get("retry_count", 0)

    if action == "web_search":
<<<<<<< HEAD
        return "web_search"
=======
        return "generate"
>>>>>>> 13da7b824cf1679b856c26d8213f656a276d558e

    if grade.get("overall_score", 0) < GRADE_THRESHOLD:
        if retry_count < MAX_RETRIES:
            return "rewrite_query"
        return "generate"

    if action == "rewrite_query":
        if retry_count < MAX_RETRIES:
            return "rewrite_query"
        return "generate"

    return "generate"