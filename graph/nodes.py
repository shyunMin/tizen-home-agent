import os
import json
import subprocess
from typing import List, Dict, Any, cast
import requests
import base64
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
# 만약 Vertex AI가 아닌 서비스(AI Studio)로 직접 연결하고자 한다면 아래 주석을 참조하세요.

import config
from graph.state import AgentState, WorkerResult
from utils.sdb_handler import execute_tizen_action, get_device_serial, get_screen_resolution
from utils.helpers import extract_json

# ---------------------------------------------------------------------------
# LangChain 모델 팩토리
# ---------------------------------------------------------------------------

# Vertex AI 인증 정보 설정 
# (시스템 환경 변수에 키 파일 경로 명시)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/home/jay/keys/shyun-gemini-project-d9fee6b62704.json"

def make_llm(model: str = "gemini-2.5-flash", **kwargs) -> ChatGoogleGenerativeAI:
    """
    최신 통합 모델 인터페이스(ChatGoogleGenerativeAI)를 사용하여 Vertex AI 프로젝트에 접근합니다.
    이를 통해 Deprecation 경고를 제거하고 성능을 최적화하며, 구글 검색 등의 도구 지원도 원활해집니다.
    """
    # Vertex AI 플랫폼(GCP) 백엔드를 사용하도록 설정
    return ChatGoogleGenerativeAI(
        model=model,
        vertexai=True,
        project="shyun-gemini-project",
        location="asia-northeast3",
        **kwargs,
    )

    # 참고: AI Studio Gemini API 키를 직접 쓰는 경우 (Vertex AI가 아님)
    # return ChatGoogleGenerativeAI(model=model, google_api_key=os.getenv("GOOGLE_API_KEY"), **kwargs)

    # [AI Studio Gemini API로 전환 시] 
    # (import ChatGoogleGenerativeAI 필요)
    # return ChatGoogleGenerativeAI(
    #     model=model,
    #     google_api_key=os.getenv("GOOGLE_API_KEY"),
    #     **kwargs,
    # )

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
        "너는 사용자의 요청을 분석하여 적절한 작업(Task)으로 분류하는 'Home Agent'야. "
        "사용자 입력에 따라 아래 Task 종류 중 가장 적절한 것들을 선택해.\n"
        "1. general_chat: 단순 인사, 일상 대화, 간단한 질문 (예: '안녕', '넌 누구니?')\n"
        "2. search: 최신 정보, 날씨, 뉴스 등 실시간 검색이 필요한 경우 (예: '오늘 서울 날씨?')\n"
        "3. device_control: Tizen 기기 제어 명령만 수행 (성공/실패 여부만 확인, 예: '볼륨 높여줘', 'WiFi 설정 열어')\n"
        "4. draw_ui: UI 레이아웃·화면 생성 요청 (예: '대시보드 그려줘', '날씨 카드 만들어')\n"
        "5. briefing: 최신 정보를 검색하여 깔끔한 카드뷰 형태의 HTML로 브리핑하거나 **다양한 분야(장소, 음식, 영상 등)의 추천**을 수행하는 경우\n"
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

async def chat_node(state: AgentState) -> Dict[str, Any]:
    """Chat Worker Node: 일반 대화 처리."""
    print("[chat_node] Running...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    system_prompt = (
        "너는 Home Agent야. "
        "친절하게 대화하며 Tizen 기기 제어, 실시간 웹 검색, 화면 분석(Vision), HTML 기반 모던 UI 생성 능력을 갖추고 있어. "
        "사용자가 화면에 무엇이 있는지 물으면 vision 기능을 사용하여 대답할 수 있음을 인지하고 대화해줘. "
        "능력을 물어보면 당당하게 소개해줘."
    )
    llm = make_llm("gemini-2.5-flash")
    response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
    result: WorkerResult = {"task": "general_chat", "text": response.content, "ui_code": ""}
    return {"worker_results": [result]}

async def briefing_node(state: AgentState) -> Dict[str, Any]:
    """Briefing Worker Node: 정보 브리핑 및 원격 배포 에이전트."""
    print("[briefing_node] Running briefing and HTML generation...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools=[{"google_search_retrieval": {}}],
    )
    
    system_prompt = (
        "당신은 구글 TV Gemini 스타일의 프리미엄 정보를 제공하는 'Home Agent'입니다. 사용자의 요청에 대해 단순 정보를 넘어 '감동적인 시각적 경험'을 제공해야 합니다.\n\n"
        "### [디자인 철학 - Google TV Gemini Style]\n"
        "1. **Glassmorphism**: 배경은 반드시 투명(`background: transparent`)으로 하고, 카드는 강력한 블러 효과(`backdrop-filter: blur(25px)`)와 반투명 다크 배경을 사용하세요.\n"
        "2. **Motion Design**: 애니메이션은 CSS Keyframes(`@keyframes`)를 기반으로 하여 JS 없이도 기본 동작이 가능하게 하세요. GSAP는 추가적인 부드러운 효과를 위해 선택적으로 사용하세요.\n"
        "3. **Visibility First**: 컨테이너에 `opacity: 0`을 직접 넣지 말고, CSS 애니메이션을 통해 나타나게 하여 라이브러리 로드 실패 시에도 데이터가 즉시 보이도록 하세요.\n"
        "4. **Premium Typography**: 'Pretendard' 혹은 'Inter' 폰트를 사용하고, 젬나이 특유의 그라데이션 텍스트(`#4285F4`, `#A142F4`, `#F442A1`)를 포인트로 활용하세요.\n"
        "5. **Interactive States**: 리모컨 포커스(또는 마우스 호버) 시 카드가 살짝 커지며 내부 광채(Glow)가 살아나도록 하세요.\n\n"
        "### [기술 스택 준수]\n"
        "- **CDN 활용**: Tailwind CSS, GSAP 3.x를 반드시 포함하세요.\n"
        "- **단일 파일**: HTML, CSS, JS를 하나의 코드 블록에 포함하세요.\n"
        "- **마크다운 금지**: 반드시 `<html>`로 시작하고 `</html>`로 끝나는 코드만 반환하세요.\n\n"
        "### [수행 단계]\n"
        "1. **시각 데이터 안정성 확보**: 검색 결과에서 실제 이미지 URL을 추출할 수 없거나 해당 주소가 무선으로 표시될 우려가 있는 경우, **절대로 임의의 주소를 생성하지 마세요.** 대신 `https://loremflickr.com/800/600/{주제_영어키워드}` 형식을 사용하여 안정적인 고화질 이미지를 카드마다 배치하세요.\n"
        "2. **정보 선정**: 가장 관련성 높고 시각적으로 매력적인 3개의 항목을 선정합니다.\n"
        "3. **맞춤형 카드 큐레이션**: 각 카드 이미지에 `onerror=\"this.style.display='none'\"`를 추가하여 로드 실패 시에도 레이아웃이 유지되도록 하세요. 이미지는 카드 상단이나 배경 등에 세련되게 배치하세요.\n"
        "- 모든 텍스트는 한국어로 작성합니다."
    )
    
    prompt = f"사용자 요청: {last_human}\n위 요청에 맞춰 최신 정보 검색 후 카드뷰 형태의 전체 HTML을 즉시 반환해줘."
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", prompt)])
        html_code = response.content.strip()
        
        if html_code.startswith("```html"):
            html_code = html_code[7:-3].strip()
        elif html_code.startswith("```"):
            html_code = html_code[3:-3].strip()
            
        text_result = "정보를 검색하여 브리핑 카드 뉴스를 생성했습니다."
    except Exception as e:
        print(f"[briefing_node] Error: {e}")
        text_result = f"브리핑 화면을 생성하거나 기기에 전송하는 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "briefing", "text": text_result, "ui_code": html_code if 'html_code' in locals() else ""}
    return {"worker_results": [result]}

async def search_node(state: AgentState) -> Dict[str, Any]:
    """Search Worker Node: Google Search 도구를 이용한 실시간 검색."""
    print("[search_node] Running real-time search...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools=[{"google_search_retrieval": {}}],
    )
    system_prompt = (
        "너는 검색 전문가야. 어떤 언어(한국어, 영어 등)로 요청받든 최신 정보를 Google 검색으로 찾아 답해줘.\n"
        "주의: 정보가 모호하더라도 사용자에게 추가 질문을 던지지 말고, 현재 가장 관련성 높거나 인기 있는 정보를 스스로 판단하여 즉시 제공하세요."
    )
    try:
        response = await llm.ainvoke([("system", system_prompt), ("human", last_human)])
        text = response.content
    except Exception as e:
        print(f"[search_node] Grounding failed ({e}), using plain LLM.")
        llm_plain = make_llm("gemini-2.5-flash")
        response = await llm_plain.ainvoke([("system", system_prompt), ("human", last_human)])
        text = response.content

    result: WorkerResult = {"task": "search", "text": text, "ui_code": ""}
    return {"worker_results": [result]}

async def device_node(state: AgentState) -> Dict[str, Any]:
    """Device Control Worker Node."""
    print("[device_node] Running device control...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    tizen_tools = build_tizen_langchain_tools()

    system_prompt = (
        "당신은 기기 제어 전문가인 'Home Agent'입니다. "
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

async def html_gen_node(state: AgentState) -> Dict[str, Any]:
    """HTML Generation Agent Node: 요청된 시나리오에 맞는 프리미엄 HTML UI를 생성합니다."""
    print("[html_gen_node] Generating Premium HTML UI...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    system_prompt = (
        "당신은 스마트 기기용 프리미엄 웹 인터페이스를 설계하는 전문 UI/UX 디자이너인 'Home Agent'입니다.\n"
        "사용자의 요청에 따라 현대적이고, 세련되며, 즉시 사용 가능한 단일 HTML/CSS 코드를 작성하세요.\n\n"
        "[디자인 원칙]\n"
        "- 배경색은 반드시 투명(background-color: transparent)으로 설정하고, 카드나 컴포넌트 단위로 배경색(#1A1A1A 등)을 부여\n"
        "- Glassmorphism, 부드러운 그림자(Box-shadow), 세련된 타이포그래피 활용\n"
        "- Tizen TV/기기 리모컨 사용성을 고려하여 버튼이나 카드 요소가 충분히 크고 명확해야 함\n"
        "- 텍스트 가독성을 위해 적절한 여백(Padding/Margin) 확보\n"
        "- CSS 애니메이션(Fade-in, Hover scale 등)을 통해 고급스러운 느낌 전달\n\n"
        "반드시 <html>로 시작하genui_node여 </html>로 끝나는 코드만 반환하세요. 마크다운 백틱은 사용하지 마세요."
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

async def search_presenter_node(state: AgentState) -> Dict[str, Any]:
    """Search Presenter Worker Node: 검색 결과를 보여주는 프레젠터."""
    print("[search_presenter_node] Running search presenter...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm(
        "gemini-2.5-flash",
        tools=[{"google_search_retrieval": {}}],
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
        print(f"[search_presenter_node] Error: {e}")
        text_result = "검색 결과를 화면에 표시하는 중 오류가 발생했습니다."

    result: WorkerResult = {"task": "search_presenter", "text": text_result, "ui_code": target_url if 'target_url' in locals() else ""}
    existing = cast(List[WorkerResult], state.get("worker_results", []))
    return {"worker_results": existing + [result]}

async def app_deploy_node(state: AgentState) -> Dict[str, Any]:
    """App Generation & Remote Deployment Worker Node (In-agent Generation)."""
    print("[app_deploy_node] Generating app and deploying...")
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    llm = make_llm("gemini-2.5-flash")

    system_prompt = (
        "당신은 홈 네트워크 및 스마트 서비스를 위한 웹 애플리케이션 개발 전문가인 'Home Agent'입니다.\n"
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
        print(f"[app_deploy_node] Error: {e}")
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

async def youtube_node(state: AgentState) -> Dict[str, Any]:
    """YouTube Worker Node: 유튜브 영상을 검색하고 HTML로 만들어 Tizen 기기에 재생을 요청합니다."""
    print("[youtube_node] Running YouTube Search and Play...")
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
        print(f"[youtube_node] Extracted search query: {search_query}")
        
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
        print(f"[youtube_node] Error: {e}")
        text_result = f"유튜브 영상 검색 및 재생 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "youtube_play", "text": text_result, "ui_code": html_code if 'html_code' in locals() else ""}
    return {"worker_results": [result]}

async def genui_node(state: AgentState) -> Dict[str, Any]:
    """Generative UI Worker Node: MCP 서버를 통해 고품질 HTML Document 로 조립하여 Tizen에 띄웁니다."""
    print("[genui_node] Generating high quality UI via MCP...")
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

        sys_prompt = f"""당신은 최고 수준의 시각화 경험을 제공하는 Generative UI 디자인 전문가입니다.
단순한 정보를 넘어, Google TV Gemini의 미학적 감각을 반영한 프리미엄 단일 HTML 문서를 작성하세요.

### [UI/UX 디자인 가이드라인]
1. 비주얼 퍼스트: 제공되는 모든 데이터 포인트에 대해 관련 이미지를 포함하세요. 이미지 주소가 불확실할 경우 `https://loremflickr.com/800/600/{{주제_영어키워드}}` 형식을 사용하여 404 에러를 원천 차단하세요.
2. 스타일: 테두리가 부드러운 Glassmorphism 카드 레이아웃, 심미적인 그라데이션 포인트 사용
3. 배경: 반드시 `background: transparent;`로 설정하여 시스템 전역 앱 느낌 유지
4. 애니메이션: GSAP 혹은 CSS Keyframes를 활용하여 AI가 정보를 '탐색'하고 '제공'하는 느낌의 세련된 트랜지션 적용
5. 기술: Tailwind CSS와 외부 라이브러리(GSAP, Lottie 등)를 활용하여 최첨단 웹 경험 구현

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
            print(f"[genui_node] MCP Error: {mcp_e}, falling back to fragment.")
            final_html = f"<html><head><script src='https://cdn.tailwindcss.com'></script></head><body>{html_fragment}</body></html>"
            
        text_result = f"요청하신 화면을 OpenGenerativeUI 기반으로 시각화했습니다."
    except Exception as e:
        print(f"[genui_node] Error: {e}")
        text_result = f"화면 시각화 중 오류가 발생했습니다: {e}"

    result: WorkerResult = {"task": "genui", "text": text_result, "ui_code": final_html if 'final_html' in locals() else ""}
    return {"worker_results": [result]}

async def vision_node(state: AgentState) -> Dict[str, Any]:
    """Vision Agent Node: 기기 화면을 캡처하고 멀티모달 LLM으로 분석하여 설명합니다."""
    print("[vision_node] Capturing screen and analyzing contents...")
    
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    
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
        print(f"[vision_node] Executing: {' '.join(cap_cmd)}")
        subprocess.run(cap_cmd, capture_output=True, check=True, timeout=15)
        
        # 2. 파일 가져오기 (Pull)
        pull_cmd = cmd_base + ["pull", remote_tmp, "."]
        subprocess.run(pull_cmd, capture_output=True, check=True, timeout=15)
        
        # 3. 이미지 읽기 및 Base64 인코딩
        if not os.path.exists(local_filename):
            raise FileNotFoundError(f"Screenshot file not found: {local_filename}")
            
        with open(local_filename, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode("utf-8")
            
        # 4. 해상도 정보 가져오기 및 LLM 분석 요청
        screen_res = get_screen_resolution()
        width, height = screen_res["width"], screen_res["height"]
        
        prompt = (
            f"사용자 요청: {last_human}\n\n"
            f"현재 기기의 화면 해상도는 {width}x{height} 입니다. 위 요청에 따라 화면을 분석하세요.\n"
            "### [응답 규칙]\n"
            f"1. **특정 객체의 위치, 좌표, 클릭 지점**을 물어보는 경우: 해당 객체의 영역 [xmin, ymin, xmax, ymax]와 중심 클릭 지점(Center X, Center Y)을 반드시 {width}x{height} 범위 내의 **픽셀(pixel) 값**으로 정확하게 답변하세요.\n"
            "2. **단순히 화면에 무엇이 있는지 물어보는 경우**: 화면의 전반적인 구성과 주요 요소들을 친절하게 설명하세요. 이 경우 **좌표값이나 숫자 정보는 답변에 절대로 포함하지 마세요.**"
        )
        
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded_image}"},
                },
            ]
        )
        
        print(f"[vision_node] Invoking Multimodal LLM (Resolution: {width}x{height})...")
        llm = make_llm("gemini-2.5-flash")
        response = await llm.ainvoke([message])
        text_result = response.content
        
        # 5. 정리 (Cleanup)
        if os.path.exists(local_filename):
            os.remove(local_filename)
        subprocess.run(cmd_base + ["shell", f"rm {remote_tmp}"], capture_output=True, timeout=5)
        
    except Exception as e:
        print(f"[vision_node] Error: {e}")
        text_result = f"화면을 캡처하거나 분석하는 중 오류가 발생했습니다: {e}"
        if os.path.exists(local_filename):
            os.remove(local_filename)

    result: WorkerResult = {"task": "vision", "text": text_result, "ui_code": ""}
    return {"worker_results": [result]}

