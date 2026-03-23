import os
import json
import subprocess
from typing import List, Dict, Any, cast
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

import config
from graph.state import AgentState, WorkerResult
from utils.sdb_handler import execute_tizen_action, get_device_serial
from utils.helpers import extract_json

# ---------------------------------------------------------------------------
# LangChain 모델 팩토리
# ---------------------------------------------------------------------------

def make_llm(model: str = "gemini-2.5-flash", **kwargs) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        **kwargs,
    )

# ---------------------------------------------------------------------------
# LangGraph ToolNode 패턴 – Tizen SDB 도구
# ---------------------------------------------------------------------------

def build_tizen_langchain_tools():
    """TIZEN_TOOLS_DATA를 기반으로 LangChain StructuredTool 목록 동적 생성."""
    from langchain_core.tools import StructuredTool
    tools = []
    for action in config.TIZEN_TOOLS_DATA:
        action_name = action.get("name", "unknown_action")
        action_desc = action.get("description", f"Tizen SDB action: {action_name}")

        def _make_fn(a_name: str):
            def _fn(arguments: dict = {}) -> str:
                result = execute_tizen_action(a_name, arguments)
                return json.dumps(result, ensure_ascii=False)
            return _fn

        fn = _make_fn(action_name)
        structured_tool = StructuredTool.from_function(
            func=fn,
            name=action_name,
            description=action_desc,
        )
        tools.append(structured_tool)
    return tools

# ---------------------------------------------------------------------------
# 그래프 노드 함수
# ---------------------------------------------------------------------------

async def router_node(state: AgentState) -> Dict[str, Any]:
    """
    Router Node: 사용자 메시지를 분석하여 수행할 Task 목록 결정.
    """
    from config import RouterResult
    print("[router_node] Analyzing intent...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    system_prompt = (
        "너는 사용자의 요청을 분석하여 적절한 작업(Task)으로 분류하는 Router Agent야. "
        "사용자 입력에 따라 아래 Task 종류 중 가장 적절한 것들을 선택해.\n"
        "1. general_chat: 단순 인사, 일상 대화, 간단한 질문 (예: '안녕', '넌 누구니?')\n"
        "2. search: 최신 정보, 날씨, 뉴스 등 실시간 검색이 필요한 경우 (예: '오늘 서울 날씨?')\n"
        "3. device_control: Tizen 기기 제어 명령만 수행 (성공/실패 여부만 확인, 예: '볼륨 높여줘', 'WiFi 설정 열어')\n"
        "4. draw_a2ui: UI 레이아웃·화면 생성 요청 (예: '대시보드 그려줘', '날씨 카드 만들어')\n"
        "여러 Task가 필요하면 모두 포함하고 intent를 'complex'로 설정해."
    )

    llm = make_llm("gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(RouterResult)

    messages = [
        ("system", system_prompt),
        ("human", last_human),
    ]

    try:
        result: RouterResult = await structured_llm.ainvoke(messages)
        tasks = result.tasks if result.tasks else ["general_chat"]
    except Exception as e:
        print(f"[router_node] Error: {e}. Falling back to general_chat.")
        tasks = ["general_chat"]

    print(f"[router_node] Tasks decided: {tasks}")
    return {"tasks": tasks}

async def chat_worker_node(state: AgentState) -> Dict[str, Any]:
    """Chat Worker Node: 일반 대화 처리."""
    print("[chat_worker_node] Running...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    system_prompt = (
        "너는 Tizen Home Agent의 Chat Worker야. "
        "친절하게 대화하며 Tizen 기기 제어, 실시간 웹 검색, A2UI 기반 화면 생성 능력을 갖추고 있어. "
        "능력을 물어보면 당당하게 소개해줘."
    )
    llm = make_llm("gemini-2.5-flash")
    response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
    result: WorkerResult = {"task": "general_chat", "text": response.content, "ui_code": ""}
    return {"worker_results": [result]}

async def search_worker_node(state: AgentState) -> Dict[str, Any]:
    """Search Worker Node: Google Search 도구를 이용한 실시간 검색."""
    print("[search_worker_node] Running real-time search...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools="google_search_retrieval",
    )
    system_prompt = (
        "너는 검색 전문가야. Google 검색을 활용하여 사용자의 질문에 대한 최신 정보를 찾아 답해줘."
    )
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
        text = response.content
    except Exception as e:
        print(f"[search_worker_node] Grounding failed ({e}), using plain LLM.")
        llm_plain = make_llm("gemini-2.5-flash")
        response = await llm_plain.ainvoke([("system", system_prompt), ("human", last_human)])
        text = response.content

    result: WorkerResult = {"task": "search", "text": text, "ui_code": ""}
    return {"worker_results": [result]}

async def device_worker_node(state: AgentState) -> Dict[str, Any]:
    """Device Control Worker Node."""
    print("[device_worker_node] Running device control...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    tizen_tools = build_tizen_langchain_tools()

    system_prompt = (
        "당신은 Tizen 기기 제어 전문가입니다. "
        "사용자의 명령에 따라 적절한 Tizen 도구를 호출하고 그 실행 결과(성공 또는 실패 여부)를 짧게 요약하세요. "
        "별도의 UI 코드나 부가 설명 없이, 기기 제어 수행 상태만 알려주면 됩니다."
    )

    if tizen_tools:
        llm = make_llm("gemini-2.5-flash").bind_tools(tizen_tools)
    else:
        llm = make_llm("gemini-2.5-flash")

    messages_for_llm = [("system", system_prompt), ("human", last_human)]
    response = await llm.ainvoke(messages_for_llm)

    tool_call_results = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc.get("args", {})
            arguments = tool_args.get("arguments", tool_args) if isinstance(tool_args, dict) else tool_args
            sdb_result = execute_tizen_action(tool_name, arguments)
            tool_call_results.append(
                ToolMessage(
                    content=json.dumps(sdb_result, ensure_ascii=False),
                    tool_call_id=tc["id"],
                    name=tool_name,
                )
            )
        followup_messages = [("system", system_prompt), ("human", last_human), response] + tool_call_results
        final_response = await llm.ainvoke(followup_messages)
        raw_content = final_response.content
    else:
        raw_content = response.content

    if isinstance(raw_content, list):
        text_parts = [p.get("text", "") if isinstance(p, dict) else p for p in raw_content]
        final_text = "\n".join(text_parts)
    else:
        final_text = str(raw_content) if raw_content is not None else ""

    # A2UI 생성 중단: 기기 제어 결과만 텍스트로 반환

    result: WorkerResult = {"task": "device_control", "text": final_text, "ui_code": ""}
    return {"worker_results": [result]}

async def a2ui_worker_node(state: AgentState) -> Dict[str, Any]:
    """A2UI Draw Worker Node."""
    print("[a2ui_worker_node] Generating A2UI layout...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    system_prompt = (
        "당신은 A2UI(Agent-to-UI) v0.9 규격의 전문 UI/UX 디자이너입니다.\n\n"
        "[A2UI v0.9 메시지 구조]\n"
        "- 출력은 반드시 JSON 메시지 객체들의 리스트(Array)여야 합니다.\n"
        "- 모든 메시지는 `version: 'v0.9'` 필드를 포함해야 합니다.\n"
        "- 첫 번째 메시지: `createSurface` (surfaceId: 'main_surface')\n"
        "- 두 번째 메시지: `updateComponents` (surfaceId: 'main_surface', components: [...])\n\n"
        "반드시 유효한 JSON 리스트만 출력하고 다른 설명은 생략하세요."
    )
    llm = make_llm("gemini-2.5-flash", temperature=0.2)
    prompt = f"사용자 요청: {last_human}\n이 요청에 맞는 A2UI 코드를 생성해줘. 프리미엄한 디자인 감각으로!"
    response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
    ui_code = extract_json(response.content)
    try:
        ui_data = json.loads(ui_code)
        if isinstance(ui_data, dict) and "messages" in ui_data:
            ui_code = json.dumps(ui_data["messages"], ensure_ascii=False)
    except Exception:
        pass
    result: WorkerResult = {"task": "draw_a2ui", "text": "요청하신 디자인을 A2UI 규격으로 생성했습니다.", "ui_code": ui_code}
    return {"worker_results": [result]}

async def search_presenter_worker_node(state: AgentState) -> Dict[str, Any]:
    """Search Presenter Worker Node: 검색 결과를 보여주는 프레젠터."""
    print("[search_presenter_worker_node] Running search presenter...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools="google_search_retrieval",
    )
    system_prompt = (
        "너는 검색 결과를 분석하여 가장 관련도가 높은 웹페이지의 URL을 추출하는 프레젠터야. "
        "사용자의 검색 의도를 파악하고, 구글 검색을 통해 알아낸 가장 적합한 단일 URL만 JSON 형식으로 반환해. "
        "응답은 반드시 `{\"url\": \"https://...\"}` 형태의 JSON이어야 해."
    )
    prompt = f"사용자 요청: {last_human}"
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
        try:
            parsed = json.loads(extract_json(response.content))
            target_url = parsed.get("url", "")
        except Exception:
            # fallback if not valid json
            target_url = ""
            
        if target_url:
            print(f"[search_presenter_worker_node] Found URL: {target_url}")
            serial = get_device_serial()
            cmd = ["sdb"]
            if serial:
                cmd.extend(["-s", serial])
            shell_cmd = f"app_launcher -s org.tizen.tizenclaw-webview __APP_SVC_URI__ {target_url}"
            cmd.extend(["shell", shell_cmd])
            
            print(f"[search_presenter_worker_node] Executing: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            text_result = f"검색 결과를 TV 화면에 띄웠습니다. (URL: {target_url})"
        else:
            text_result = "적절한 검색 결과 URL을 찾지 못해 화면에 띄우지 못했습니다."
            
    except Exception as e:
        print(f"[search_presenter_worker_node] Error: {e}")
        text_result = "검색 결과를 화면에 표시하는 중 오류가 발생했습니다."

    result: WorkerResult = {"task": "search_presenter", "text": text_result, "ui_code": ""}
    existing = cast(List[WorkerResult], state.get("worker_results", []))
    return {"worker_results": existing + [result]}

async def reconstructor_node(state: AgentState) -> Dict[str, Any]:
    """Reconstructor Node."""
    print("[reconstructor_node] Merging worker results...")
    worker_results: List[WorkerResult] = cast(List[WorkerResult], state.get("worker_results", []))
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    if not worker_results:
        return {"final_text": "처리 중 오류가 발생했습니다.", "ui_code": "", "messages": [AIMessage(content="처리 중 오류가 발생했습니다.")]}

    if len(worker_results) == 1:
        only = worker_results[0]
        final_text = only["text"]
        ui_code = only["ui_code"]
    else:
        worker_summary = "\n\n".join(f"[{r['task']} 결과]\n{r['text']}" for r in worker_results)
        system_prompt = (
            "너는 다수의 AI 워커 결과를 통합하는 Reconstructor야. "
            "아래 각 워커의 결과를 자연스럽게 하나의 답변으로 합쳐줘. "
            "사용자 원래 요청: {last_human}"
        )
        llm = make_llm("gemini-2.5-flash")
        response = await llm.ainvoke([("system", system_prompt), ("human", worker_summary)])
        final_text = response.content
        all_ui = []
        for r in worker_results:
            if r["ui_code"]:
                try:
                    p = json.loads(r["ui_code"])
                    all_ui.extend(p if isinstance(p, list) else [p])
                except: pass
        ui_code = json.dumps(all_ui, ensure_ascii=False) if all_ui else ""

    print(f"[reconstructor_node] Final text length: {len(final_text)}")
    return {"final_text": final_text, "ui_code": ui_code, "messages": [AIMessage(content=final_text)]}
