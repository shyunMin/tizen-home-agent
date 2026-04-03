from langgraph.graph import StateGraph, START, END
from graph.state import AgentState
from graph.nodes import (
    router_node,
    chat_node,
    search_node,
    device_node,
    html_gen_node,
    search_presenter_node,
    briefing_node,
    app_deploy_node,
    youtube_node,
    genui_node,
    vision_node,
    html_synthesizer_node,
)

def route_to_workers(state: AgentState):
    """
    Router 결과의 tasks에 따라 적절한 워커 노드(들)로 분기.
    """
    tasks = state.get("tasks", ["general_chat"])
    task_node_map = {
        "general_chat": "chat_node",
        "search": "search_node",
        "device_control": "device_node",
        "draw_ui": "html_gen_node",
        "briefing": "briefing_node",
        "app_deploy": "app_deploy_node",
        "youtube_play": "youtube_node",
        "genui": "genui_node",
        "vision": "vision_node",
    }
    targets = []
    for t in tasks:
        if t == "search":
            targets.extend(["search_node", "search_presenter_node"])
        else:
            targets.append(task_node_map.get(t, "chat_node"))
            
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
    graph.add_node("chat_node", chat_node)
    graph.add_node("search_node", search_node)
    graph.add_node("search_presenter_node", search_presenter_node)
    graph.add_node("briefing_node", briefing_node)
    graph.add_node("app_deploy_node", app_deploy_node) ##
    graph.add_node("youtube_node", youtube_node)
    graph.add_node("genui_node", genui_node)
    graph.add_node("vision_node", vision_node)
    graph.add_node("device_node", device_node)
    graph.add_node("html_gen_node", html_gen_node)
    graph.add_node("html_synthesizer_node", html_synthesizer_node)

    graph.add_edge(START, "router_node")

    graph.add_conditional_edges(
        "router_node",
        route_to_workers,
        {
            "chat_node": "chat_node",
            "search_node": "search_node",
            "search_presenter_node": "search_presenter_node",
            "briefing_node": "briefing_node",
            "app_deploy_node": "app_deploy_node",
            "youtube_node": "youtube_node",
            "genui_node": "genui_node",
            "device_node": "device_node",
            "html_gen_node": "html_gen_node",
            "vision_node": "vision_node",
        },
    )

    for worker in [
        "chat_node",
        "search_node",
        "search_presenter_node",
        "briefing_node",
        "app_deploy_node",
        "youtube_node",
        "genui_node",
        "vision_node",
        "device_node",
        "html_gen_node",
    ]:
        graph.add_edge(worker, "html_synthesizer_node")

    graph.add_edge("html_synthesizer_node", END)
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
    router_node --> chat_node["💬 Chat Node"]
    router_node --> search_node["🔍 Search Node"]
    router_node --> search_presenter_node["📺 Search Presenter"]
    router_node --> briefing_node["📰 Briefing Node"]
    router_node --> app_deploy_node["📦 App Deploy Node"]
    router_node --> youtube_node["▶️ YouTube Node"]
    router_node --> genui_node["✨ GenUI Node"]
    router_node --> vision_node["👁️ Vision Node"]
    router_node --> device_node["📱 Device Node"]
    router_node --> html_gen_node["🎨 HTML Gen Node"]
    chat_node --> html_synthesizer_node["🔄 HTML Synthesizer"]
    search_node --> html_synthesizer_node
    search_presenter_node --> html_synthesizer_node
    briefing_node --> html_synthesizer_node
    app_deploy_node --> html_synthesizer_node
    youtube_node --> html_synthesizer_node
    genui_node --> html_synthesizer_node
    vision_node --> html_synthesizer_node
    device_node --> html_synthesizer_node
    html_gen_node --> html_synthesizer_node
    html_synthesizer_node --> END([✅ END])
"""
