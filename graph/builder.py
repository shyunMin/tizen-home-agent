from langgraph.graph import StateGraph, START, END
from graph.state import AgentState
from graph.nodes import (
    router_node,
    chat_worker_node,
    search_worker_node,
    device_worker_node,
    a2ui_worker_node,
    search_presenter_worker_node,
    search_presenter_worker_node,
    briefing_worker_node,
    app_deploy_worker_node,
    youtube_worker_node,
    genui_worker_node,
    synthesizer_node,
)

def route_to_workers(state: AgentState):
    """
    Router 결과의 tasks에 따라 적절한 워커 노드(들)로 분기.
    """
    tasks = state.get("tasks", ["general_chat"])
    task_node_map = {
        "general_chat": "chat_worker_node",
        "search": "search_worker_node",
        "device_control": "device_worker_node",
        "draw_a2ui": "a2ui_worker_node",
        "briefing": "briefing_worker_node",
        "app_deploy": "app_deploy_worker_node",
        "youtube_play": "youtube_worker_node",
        "genui": "genui_worker_node",
    }
    targets = []
    for t in tasks:
        if t == "search":
            targets.extend(["search_worker_node", "search_presenter_worker_node"])
        else:
            targets.append(task_node_map.get(t, "chat_worker_node"))
            
    seen = set()
    unique_targets = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            unique_targets.append(t)

    print(f"[route_to_workers] Routing to: {unique_targets}")
    if len(unique_targets) == 1:
        return unique_targets[0]
    return unique_targets

def build_graph() -> StateGraph:
    """AgentState 기반의 StateGraph 구성 및 컴파일."""
    graph = StateGraph(AgentState)

    graph.add_node("router_node", router_node)
    graph.add_node("chat_worker_node", chat_worker_node)
    graph.add_node("search_worker_node", search_worker_node)
    graph.add_node("search_presenter_worker_node", search_presenter_worker_node)
    graph.add_node("briefing_worker_node", briefing_worker_node)
    graph.add_node("app_deploy_worker_node", app_deploy_worker_node)
    graph.add_node("youtube_worker_node", youtube_worker_node)
    graph.add_node("genui_worker_node", genui_worker_node)
    graph.add_node("device_worker_node", device_worker_node)
    graph.add_node("a2ui_worker_node", a2ui_worker_node)
    graph.add_node("synthesizer_node", synthesizer_node)

    graph.add_edge(START, "router_node")

    graph.add_conditional_edges(
        "router_node",
        route_to_workers,
        {
            "chat_worker_node": "chat_worker_node",
            "search_worker_node": "search_worker_node",
            "search_presenter_worker_node": "search_presenter_worker_node",
            "briefing_worker_node": "briefing_worker_node",
            "app_deploy_worker_node": "app_deploy_worker_node",
            "youtube_worker_node": "youtube_worker_node",
            "genui_worker_node": "genui_worker_node",
            "device_worker_node": "device_worker_node",
            "a2ui_worker_node": "a2ui_worker_node",
        },
    )

    for worker in [
        "chat_worker_node",
        "search_worker_node",
        "search_presenter_worker_node",
        "briefing_worker_node",
        "app_deploy_worker_node",
        "youtube_worker_node",
        "genui_worker_node",
        "device_worker_node",
        "a2ui_worker_node",
    ]:
        graph.add_edge(worker, "synthesizer_node")

    graph.add_edge("synthesizer_node", END)
    return graph.compile()

def get_mermaid_diagram(compiled_graph) -> str:
    """LangGraph 그래프를 Mermaid 형식으로 내보냅니다."""
    if compiled_graph is None:
        return "graph not initialized"
    try:
        return compiled_graph.get_graph().draw_mermaid()
    except:
        return """
graph TD
    START([🚀 START]) --> router_node["🧭 Router Node"]
    router_node --> chat_worker_node["💬 Chat Worker"]
    router_node --> search_worker_node["🔍 Search Worker"]
    router_node --> search_presenter_worker_node["📺 Search Presenter"]
    router_node --> briefing_worker_node["📰 Briefing Worker"]
    router_node --> app_deploy_worker_node["📦 App Deploy Worker"]
    router_node --> youtube_worker_node["▶️ YouTube Worker"]
    router_node --> genui_worker_node["✨ GenUI Worker"]
    router_node --> device_worker_node["📱 Device Worker"]
    router_node --> a2ui_worker_node["🎨 A2UI Worker"]
    chat_worker_node --> synthesizer_node["🔄 Synthesizer"]
    search_worker_node --> synthesizer_node
    search_presenter_worker_node --> synthesizer_node
    briefing_worker_node --> synthesizer_node
    app_deploy_worker_node --> synthesizer_node
    youtube_worker_node --> synthesizer_node
    genui_worker_node --> synthesizer_node
    device_worker_node --> synthesizer_node
    a2ui_worker_node --> synthesizer_node
    synthesizer_node --> END([✅ END])
"""
