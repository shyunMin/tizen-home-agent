import os
import json
import subprocess
from typing import List, Dict, Any, cast
import requests
import base64
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
        "4. draw_ui: UI 레이아웃·화면 생성 요청 (예: '대시보드 그려줘', '날씨 카드 만들어')\n"
        "5. briefing: 최신 정보를 검색하여 깔끔한 카드 뉴스 형태의 HTML로 브리핑하는 경우 (**유튜브에서 볼만한 영상 추천** 포함)\n"
        "6. app_deploy: Tizen용 HTML 앱을 생성하고 배포하는 경우\n"
        "7. youtube_play: 사용자가 **특정 영상** (제목이 명시되거나 '틀어줘', '재생해줘' 등)을 즉시 시청하고자 하는 의도가 명확한 경우\n"
        "8. genui: 고품질 시각화 화면 생성\n"
        "9. vision: 사용자가 현재 기기의 **화면 분석 및 설명**을 요청하는 경우 (예: '화면에 뭐 있어?', '지금 화면 읽어줘')\n"
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
        "너는 Tizen Home Agent의 Chat Agent야. "
        "친절하게 대화하며 Tizen 기기 제어, 실시간 웹 검색, 화면 분석(Vision), HTML 기반 모던 UI 생성 능력을 갖추고 있어. "
        "사용자가 화면에 무엇이 있는지 물으면 vision 기능을 사용하여 대답할 수 있음을 인지하고 대화해줘. "
        "능력을 물어보면 당당하게 소개해줘."
    )
    llm = make_llm("gemini-2.5-flash")
    response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
    result: WorkerResult = {"task": "general_chat", "text": response.content, "ui_code": ""}
    return {"worker_results": [result]}

async def briefing_worker_node(state: AgentState) -> Dict[str, Any]:
    """Briefing Worker Node: 정보 브리핑 및 원격 배포 에이전트."""
    print("[briefing_worker_node] Running briefing and HTML generation...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools="google_search_retrieval",
    )
    
    system_prompt = (
        "당신은 사용자의 질문에 대해 실시간 정보를 검색하고, 이를 Tizen OS 장치에서 즉시 확인할 수 있는 현대적인 카드 뉴스 스타일의 HTML로 변환하여 배포하는 'Tizen UI/UX 에이전트'입니다.\n\n"
        "### [수행 단계]\n"
        "1. 정보 검색: 사용자의 요청 사항에 부합하는 최신 정보를 검색하여 핵심 데이터(제목, 설명, 이미지 URL, 관련 링크)를 최소 3~5개 추출하세요.\n"
        "2. 맞춤형 HTML 생성: 아래 [디자인 가이드라인]을 준수하여, 검색된 주제에 최적화된 '카드 뷰' 레이아웃의 단일 HTML 코드를 작성하세요. "
        "응답은 반드시 `<html`로 시작하고 `</html>`로 끝나는 코드여야 하며, 백틱(```html) 등 마크다운 블록 문법을 일절 포함하지 마세요.\n\n"
        "### [디자인 가이드라인]\n"
        "- 테마: 배경색 투명(background-color: transparent) 기반, 화이트/그레이 텍스트 및 카드 배경색 사용.\n"
        "- 레이아웃: '카드(Card)' 기반 디자인. CSS Grid 또는 Flexbox를 사용하여 항목별로 독립된 카드 형태 구성.\n"
        "- 시각 요소:\n"
        "    - 각 카드 상단에 검색된 이미지(썸네일) 배치 (`object-fit: cover` 사용). 이미지가 없을 경우 무미건조한 회색 배경(`background: #333;`)을 사용하세요.\n"
        "    - 제목(Bold), 요약 내용, 출처 정보를 명확히 구분.\n"
        "    - 하단에 '자세히 보기' 또는 '링크 이동' 버튼 배치(스타일만 적용된 <a> 태그).\n"
        "- 애니메이션: 마우스 오버(혹은 포커스) 시 카드가 살짝 커지는(transform: scale(1.05)) 효과 추가 (Tizen TV 리모컨 사용성 고려).\n"
        "- 텍스트 처리: 텍스트가 너무 길어지면 `display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;` 처리를 하여 카드 크기를 일정하게 유지하세요.\n"
        "- 유튜브 특화 기능: 유튜브 영상 추천 요청의 경우, 각 카드의 이미지 영역에 영상 썸네일을 배치하고 클릭 시 해당 유튜브 영상(`youtube.com/watch?v=...`)으로 즉시 이동할 수 있는 버튼을 포함하세요.\n"
        "- 모든 콘텐츠는 한국어로 작성합니다."
    )
    
    prompt = f"사용자 요청: {last_human}\n위 요청에 맞춰 최신 정보 검색 후 카드 뉴스 형태의 전체 HTML을 즉시 반환해줘."
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
        html_code = response.content.strip()
        
        if html_code.startswith("```html"):
            html_code = html_code[7:-3].strip()
        elif html_code.startswith("```"):
            html_code = html_code[3:-3].strip()
            
        text_result = "정보를 검색하여 브리핑 카드 뉴스를 생성했습니다."
    except Exception as e:
        print(f"[briefing_worker_node] Error: {e}")
        text_result = f"브리핑 화면을 생성하거나 기기에 전송하는 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "briefing", "text": text_result, "ui_code": html_code if 'html_code' in locals() else ""}
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

async def html_gen_worker_node(state: AgentState) -> Dict[str, Any]:
    """HTML Generation Agent Node: 요청된 시나리오에 맞는 프리미엄 HTML UI를 생성합니다."""
    print("[html_gen_worker_node] Generating Premium HTML UI...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    system_prompt = (
        "당신은 Tizen 기기용 프리미엄 웹 인터페이스를 설계하는 전문 UI/UX 디자이너입니다.\n"
        "사용자의 요청에 따라 현대적이고, 세련되며, 즉시 사용 가능한 단일 HTML/CSS 코드를 작성하세요.\n\n"
        "[디자인 원칙]\n"
        "- 배경색은 반드시 투명(background-color: transparent)으로 설정하고, 카드나 컴포넌트 단위로 배경색(#1A1A1A 등)을 부여\n"
        "- Glassmorphism, 부드러운 그림자(Box-shadow), 세련된 타이포그래피 활용\n"
        "- Tizen TV/기기 리모컨 사용성을 고려하여 버튼이나 카드 요소가 충분히 크고 명확해야 함\n"
        "- 텍스트 가독성을 위해 적절한 여백(Padding/Margin) 확보\n"
        "- CSS 애니메이션(Fade-in, Hover scale 등)을 통해 고급스러운 느낌 전달\n\n"
        "반드시 <html>로 시작하여 </html>로 끝나는 코드만 반환하세요. 마크다운 백틱은 사용하지 마세요."
    )
    llm = make_llm("gemini-2.5-flash", temperature=0.3)
    prompt = f"사용자 요청: {last_human}\n이 요청에 맞는 가장 아름다운 UI 화면을 HTML 전체 코드로 생성해줘."
    response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
    
    html_code = response.content.strip()
    # 백틱 제거 (안전 장치)
    if html_code.startswith("```html"):
        html_code = html_code[7:-3].strip()
    elif html_code.startswith("```"):
        html_code = html_code[3:-3].strip()

    result: WorkerResult = {"task": "draw_ui", "text": "요청하신 UI 디자인을 프리미엄 HTML로 생성했습니다.", "ui_code": html_code}
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
            
            text_result = f"검색 결과 관련 URL을 추출했습니다. (URL: {target_url})"
        else:
            text_result = "적절한 검색 결과 URL을 찾지 못했습니다."
            
    except Exception as e:
        print(f"[search_presenter_worker_node] Error: {e}")
        text_result = "검색 결과를 화면에 표시하는 중 오류가 발생했습니다."

    result: WorkerResult = {"task": "search_presenter", "text": text_result, "ui_code": target_url if 'target_url' in locals() else ""}
    existing = cast(List[WorkerResult], state.get("worker_results", []))
    return {"worker_results": existing + [result]}

async def app_deploy_worker_node(state: AgentState) -> Dict[str, Any]:
    """App Generation & Remote Deployment Worker Node (In-agent Generation)."""
    print("[app_deploy_worker_node] Generating app and deploying...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm("gemini-2.5-flash")

    system_prompt = (
        "당신은 Tizen OS용 웹 애플리케이션 개발 전문가입니다.\n"
        "사용자의 요청에 따라 단일 HTML 파일로 작동하는 완성도 높은 웹 앱 코드를 작성하세요.\n\n"
        "[개발 가이드라인]\n"
        "- 모든 HTML, CSS, JavaScript를 하나의 파일에 포함하세요.\n"
        "- 배경색은 투명(transparent)으로 설정하고, 개별 UI 요소에 디자인 테마를 적용하세요.\n"
        "- Tizen TV/기기 리모컨 사용성을 고려하여 버튼 등의 요소가 충분히 크고 포커스 효과가 있어야 합니다.\n"
        "- 라이브러리는 CDN을 통해 로드할 수 있습니다 (Font Awesome, Google Fonts, Tailwind CSS 등).\n"
        "- 응답은 반드시 <html>로 시작해서 </html>로 끝나는 유효한 HTML 코드만 반환하세요.\n"
        "- 마크다운 백틱(```html)을 사용하지 마세요."
    )

    prompt = f"사용자 요청: {last_human}\n위 요청에 부합하는 최고의 Tizen 웹 앱 코드를 HTML로 작성해줘."
    
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
        html_code = response.content.strip()
        
        # 백틱 제거 (안전 장치)
        if html_code.startswith("```html"):
            html_code = html_code[7:-3].strip()
        elif html_code.startswith("```"):
            html_code = html_code[3:-3].strip()

        text_result = (
            f"요청하신 '{last_human}' 앱 코드를 생성했습니다.\n"
        )
        
    except Exception as e:
        print(f"[app_deploy_worker_node] Error: {e}")
        text_result = f"앱 생성 및 배포 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "app_deploy", "text": text_result, "ui_code": html_code if 'html_code' in locals() else ""}
    return {"worker_results": [result]}

async def html_synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """HTML Synthesizer Node (구 Synthesizer/Reconstructor): 워커 결과 텍스트 통합 및 순수 HTML 병합"""
    print("[html_synthesizer_node] Merging worker results...")
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
            "너는 다수의 AI 워커 결과를 통합하는 결과 통합기야. "
            "아래 각 워커의 결과를 자연스럽게 하나의 답변으로 합쳐줘. "
            "사용자 원래 요청: {last_human}"
        )
        llm = make_llm("gemini-2.5-flash")
        response = await llm.ainvoke([("system", system_prompt), ("human", worker_summary)])
        final_text = response.content
        
        # ui_code는 JSON 파싱 없이 순수 HTML들을 그냥 이어 붙입니다.
        all_html = []
        for r in worker_results:
            if r["ui_code"]:
                all_html.append(r["ui_code"])
        ui_code = "\n".join(all_html)

    print(f"[html_synthesizer_node] Final text length: {len(final_text)}")
    return {"final_text": final_text, "ui_code": ui_code, "messages": [AIMessage(content=final_text)]}

async def youtube_worker_node(state: AgentState) -> Dict[str, Any]:
    """YouTube Worker Node: 유튜브 영상을 검색하고 HTML로 만들어 Tizen 기기에 재생을 요청합니다."""
    print("[youtube_worker_node] Running YouTube Search and Play...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm("gemini-2.5-flash")
    system_prompt = (
        "당신은 유튜브 검색어 추출기입니다. 사용자의 입력에서 유튜브에 검색할 '가장 적절한 검색어' 딱 하나만 추출해서 문자열로 반환하세요.\n"
        "예: '아이유 좋은날 유튜브에서 재생해줘' -> '아이유 좋은날'\n"
        "응답에는 부가 설명 없이 오직 검색어만 출력하세요."
    )
    
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
        search_query = response.content.strip()
        print(f"[youtube_worker_node] Extracted search query: {search_query}")
        
        from youtube_search import YoutubeSearch
        results = YoutubeSearch(search_query, max_results=1).to_dict()
        
        if not results:
            return {"worker_results": [{"task": "youtube_play", "text": "유튜브에서 관련된 영상을 찾지 못했습니다.", "ui_code": ""}]}
        
        video_id = results[0]["id"]
        video_title = results[0]["title"]
        
        html_code = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{video_title}</title>
<style>
  body, html {{ margin: 0; padding: 0; width: 100%; height: 100%; min-height: 500px; background-color: transparent; overflow: hidden; display: flex; justify-content: center; align-items: center; color: white; font-family: sans-serif; }}
  h1 {{ font-size: 2rem; }}
</style>
<script>
  // 퍼가기 금지 영상(오류 153)을 우회하기 위해 유튜브 실제 페이지로 직접 이동합니다.
  setTimeout(function() {{
      window.location.replace("https://www.youtube.com/watch?v={video_id}");
  }}, 800);
</script>
</head>
<body>
  <h1>유튜브로 이동하는 중입니다...</h1>
</body>
</html>"""

        text_result = f"유튜브에서 '{video_title}' 영상을 찾았습니다."
        
    except Exception as e:
        print(f"[youtube_worker_node] Error: {e}")
        text_result = f"유튜브 영상 검색 및 재생 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "youtube_play", "text": text_result, "ui_code": html_code if 'html_code' in locals() else ""}
    return {"worker_results": [result]}

async def genui_worker_node(state: AgentState) -> Dict[str, Any]:
    """Generative UI Worker Node: MCP 서버를 통해 고품질 HTML Document 로 조립하여 Tizen에 띄웁니다."""
    print("[genui_worker_node] Generating high quality UI via MCP...")
    last_human = next((m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")

    try:
        # LLM으로 HTML Fragment 생성 지시
        llm = make_llm("gemini-2.5-flash")
        
        # OpenGenerativeUI의 마스터 프롬프트를 읽어서 주입 (애니메이션, JS 로직 구현을 위함)
        playbook_path = os.path.join("OpenGenerativeUI", "apps", "mcp", "skills", "master-agent-playbook.txt")
        if os.path.exists(playbook_path):
            with open(playbook_path, "r", encoding="utf-8") as f:
                playbook_text = f.read()
        else:
            playbook_text = ""

        sys_prompt = f"""당신은 Tailwind CSS 기반의 세련된 UI 시각화 컴포넌트 생성기입니다. 
아래의 Playbook 지침을 엄격하게 따라 애니메이션과 Javascript(인터랙션)가 포함된 완벽한 단일 HTML fragment(body 내부 구조)를 작성하세요.
절대로 ```html 같은 마크다운 기호 없이 순수 HTML 태그 구조 텍스트만 출력하세요. 
배경색은 반드시 투명(transparent)하게 처리하세요.

---
{playbook_text}
"""
        
        response = await llm.ainvoke([("system", sys_prompt), ("human", last_human)])
        html_fragment = response.content.strip()
        if html_fragment.startswith("```html"):
            html_fragment = html_fragment[7:-3].strip()
        elif html_fragment.startswith("```"):
            html_fragment = html_fragment[3:-3].strip()

        # MCP 서버를 통해 완전한 HTML Document 로 조립 ('assemble_document' tool)
        final_html = html_fragment # fallback
        try:
            from mcp.client.stdio import stdio_client, StdioServerParameters
            from mcp import ClientSession

            mcp_dir = os.path.abspath(os.path.join("OpenGenerativeUI", "apps", "mcp"))
            server_params = StdioServerParameters(
                command="npx",
                args=["tsx", "src/stdio.ts"],
                cwd=mcp_dir
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("assemble_document", arguments={
                        "title": "Generative UI",
                        "description": last_human,
                        "html": html_fragment
                    })
                    final_html = result.content[0].text
                    
            # Tizen 단독 실행을 위해 Tailwind CSS CDN 주입 및 CSP 속성 완화
            if "</head>" in final_html:
                # CSP에서 tailwindcdn 허용을 위해 unsafe-eval 뒤에 추가
                final_html = final_html.replace(
                    "https://cdnjs.cloudflare.com", 
                    "https://cdnjs.cloudflare.com\n      https://cdn.tailwindcss.com"
                )
                # Tailwind 스크립트 추가
                final_html = final_html.replace(
                    "</head>", 
                    "  <script src=\"https://cdn.tailwindcss.com\"></script>\n</head>"
                )
        except Exception as mcp_e:
            import traceback
            print(f"[genui_worker_node] MCP Error: {mcp_e}, falling back to fragment.")
            final_html = f"<html><head><script src='https://cdn.tailwindcss.com'></script></head><body>{html_fragment}</body></html>"
            
        text_result = f"요청하신 화면을 OpenGenerativeUI 기반으로 시각화했습니다."
    except Exception as e:
        print(f"[genui_worker_node] Error: {e}")
        text_result = f"화면 시각화 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "genui", "text": text_result, "ui_code": final_html if 'final_html' in locals() else ""}
    return {"worker_results": [result]}

async def vision_worker_node(state: AgentState) -> Dict[str, Any]:
    """Vision Agent Node: 기기 화면을 캡처하고 멀티모달 LLM으로 분석하여 설명합니다."""
    print("[vision_worker_node] Capturing screen and analyzing contents...")
    
    serial = get_device_serial()
    local_filename = f"capture_{serial}.png"
    remote_tmp = f"/tmp/{local_filename}"
    
    try:
        # 1. 캡처 명령 실행 (enlightenment_info 사용)
        cmd_base = ["sdb"]
        if serial: cmd_base.extend(["-s", serial])
        
        # 권한 확보 시도 (옵션)
        subprocess.run(cmd_base + ["root", "on"], capture_output=True, timeout=5)
        
        # 캡처
        cap_cmd = cmd_base + ["shell", f'enlightenment_info -dump_screen winfo -p /tmp/ -n {local_filename}']
        print(f"[vision_worker_node] Executing: {' '.join(cap_cmd)}")
        subprocess.run(cap_cmd, capture_output=True, check=True, timeout=15)
        
        # 2. 파일 가져오기 (Pull)
        pull_cmd = cmd_base + ["pull", remote_tmp, "."]
        subprocess.run(pull_cmd, capture_output=True, check=True, timeout=15)
        
        # 3. 이미지 읽기 및 Base64 인코딩
        if not os.path.exists(local_filename):
            raise FileNotFoundError(f"Screenshot file not found: {local_filename}")
            
        with open(local_filename, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")
            
        # 4. LLM 멀티모달 분석 요청
        llm = make_llm("gemini-2.5-flash") # 비전 기능 강화를 위해 2.5-flash 사용
        
        prompt = "이 사진은 현재 Tizen 기기 화면이야. 화면에 어떤 앱이나 콘텐츠가 실행 중인지, 텍스트나 아이콘 정보가 무엇인지 상세하게 분석해서 설명해줘."
        
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_image}"},
                },
            ]
        )
        
        print("[vision_worker_node] Invoking Multimodal LLM...")
        response = await llm.ainvoke([message])
        text_result = response.content
        
        # 5. 정리 (Cleanup)
        if os.path.exists(local_filename):
            os.remove(local_filename)
        subprocess.run(cmd_base + ["shell", f"rm {remote_tmp}"], capture_output=True, timeout=5)
        
    except Exception as e:
        print(f"[vision_worker_node] Error: {e}")
        text_result = f"화면을 캡처하거나 분석하는 중 오류가 발생했습니다: {e}"
        if os.path.exists(local_filename):
            os.remove(local_filename)

    result: WorkerResult = {"task": "vision", "text": text_result, "ui_code": ""}
    return {"worker_results": [result]}

