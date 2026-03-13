import os
import subprocess
import uvicorn
import json
import asyncio
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types

# .env 파일에서 환경 변수 로드
load_dotenv()

# 전역 변수
TIZEN_TOOLS_DATA = []
client = None
AI_RESPONSE_TIMEOUT = 30  # 개별 에이전트 응답 최대 대기 시간

# --- 유틸리티 함수 ---

def extract_json(text: str):
    """텍스트에서 JSON 블록을 추출합니다."""
    # ```json ... ``` 패턴 매칭
    json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()
    # 객체 패턴 { ... } 혹은 배열 패턴 [ ... ] 매칭 (간단한 형태)
    obj_match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if obj_match:
        return obj_match.group(1).strip()
    return text.strip()

# --- 도구 기능 정의 (Worker용) ---

def discover_tizen_tools():
    """SDB를 통해 디바이스에서 사용 가능한 도구 목록과 스키마를 가져옵니다."""
    try:
        print("Discovering Tizen tools via SDB...")
        result = subprocess.run(
            ["sdb", "shell", "action-tool list-actions"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15
        )
        
        actions = []
        raw_output = result.stdout
        # "name : " (공백 포함)으로 섹션 분리
        sections = raw_output.split("\nname : ")
        
        for section in sections:
            # 첫 섹션에 "name : "이 없을 수 있으므로 보정
            if section.startswith("name : "):
                section = section[7:]
            
            if "schema :" not in section:
                continue
            
            try:
                # 명칭 추출 (첫 줄)
                lines = section.split("\n")
                name = lines[0].strip()
                
                # schema : 이후의 본문 추출
                schema_content = section.split("schema :")[1].strip()
                
                # "test successful" 혹은 다음 섹션 시작 전까지만 남기기
                # 보통 JSON은 { 로 시작해서 } 로 끝나므로 이를 활용하거나
                # 줄 단위로 파싱하여 유효한 JSON을 찾음
                
                # 가장 간단한 방법: "test successful" 제거
                json_str = schema_content.split("... test successful")[0].strip()
                
                # 만약 뒷부분에 다른 텍스트가 붙어있다면 JSON 끝 문자 '}'를 기준으로 자름
                last_brace = json_str.rfind("}")
                if last_brace != -1:
                    json_str = json_str[:last_brace+1]

                action_schema = json.loads(json_str)
                actions.append(action_schema)
            except Exception as e:
                print(f"Error parsing tool section ({section[:20]}...): {e}")
                
        return actions
    except Exception as e:
        print(f"Tool discovery failed: {e}")
        return []

def execute_tizen_action(name: str, arguments: dict):
    """Tizen 디바이스에 액션을 전달합니다."""
    payload = {
        "id": 1,
        "params": {
            "name": name,
            "arguments": arguments
        }
    }
    json_data = json.dumps(payload, separators=(',', ':'))
    full_command = f"action-tool execute '{json_data}'"
    
    print(f"[Worker:Device] Executing: {full_command}")
    try:
        result = subprocess.run(
            ["sdb", "shell", full_command],
            capture_output=True,
            text=True,
            check=True,
            timeout=15
        )
        return {
            "status": "success", 
            "output": result.stdout.strip(), 
            "action": name
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- 에이전트 단계별 정의 ---

async def router_agent(message: str):
    """1단계: 분류 에이전트 (Router Agent)"""
    system_instruction = (
        "너는 사용자의 요청을 분석하여 적절한 작업(Task)으로 분류하는 Router Agent야. "
        "사용자의 입력을 분석하여 아래 Task 종류 중 가장 적절한 것들을 리스트로 추출해."
        "\n1. general_chat: 단순 인사, 일상적 대화, 감정 표현, 간단한 질문 등 외부 도구나 검색이 필요 없는 경우. (예: '안녕', '고마워', '넌 누구니?')"
        "\n2. search: 최신 정보, 뉴스, 날씨, 인물 정보 등 실시간 검색이나 지식이 필요한 구체적인 질문. (예: '오늘 서울 날씨 어때?', '최신 삼성 폰 알려줘')"
        "\n3. device_control_a2ui: Tizen 기기 제어 명령. (예: '볼륨 높여줘', 'TV 꺼줘', '와이파이 설정 열어')"
        "\n4. draw_a2ui: UI 디자인, 화면 레이아웃 생성 요청. (예: '대시보드 그려줘', '날씨 카드 디자인해줘')"
        "\n\n응답은 반드시 아래 JSON 형식으로만 해. 다른 텍스트는 절대 포함하지 마."
        "\n출력 예시: {\"intent\": \"simple/complex\", \"tasks\": [\"general_chat\"]}"
    )
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json"
    )
    
    response = await client.aio.models.generate_content(
        model='gemini-2.5-flash',
        contents=message,
        config=config
    )
    try:
        data = json.loads(response.text)
        # 만약 tasks가 비어있다면 최소한 general_chat이라도 수행하도록 보정
        if not data.get("tasks"):
            data["tasks"] = ["general_chat"]
        return data
    except:
        return {"intent": "simple", "tasks": ["general_chat"]}

async def chat_worker(message: str):
    """2단계 워커: 일반 대화 에이전트 (Chat Agent)"""
    print(f"[Worker:Chat] Handling message: {message}")
    response = await client.aio.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"사용자와 친절하게 대화해줘. (인사, 질문 답변 등)\n사용자: {message}"
    )
    return {"text": response.text, "ui_code": ""}

async def search_worker(message: str):
    """2단계 워커: 검색 에이전트 (Search Agent)"""
    print(f"[Worker:Search] Real-time searching for: {message}")
    
    # 검색 워커 전용 설정 (구글 검색 도구 포함)
    # 워커가 분리되어 있으므로 여기서는 Built-in Tool인 google_search를 사용할 수 있습니다.
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction="너는 검색 전문가야. 구글 검색 엔진을 사용하여 사용자의 질문에 대한 가장 최신의 정확한 정보를 찾아 답변해줘."
    )
    
    response = await client.aio.models.generate_content(
        model='gemini-2.5-flash', # 검색 워커에서도 2.5-flash 모델 사용
        contents=message,
        config=config
    )
    return {"text": response.text, "ui_code": ""}

async def device_control_worker(message: str):
    """2단계 워커: 디바이스 제어 에이전트 (Device Control Agent)"""
    # 사용 가능한 도구들을 Function Declaration으로 변환
    tizen_decls = []
    for tool in TIZEN_TOOLS_DATA:
        tizen_decls.append({
            "name": tool["name"],
            "description": tool.get("description", f"Tizen control action: {tool['name']}"),
            "parameters": tool["inputSchema"]
        })
    
    config = types.GenerateContentConfig(
        system_instruction="사용자의 명령에 따라 적절한 Tizen 도구를 호출하고, 실행 결과를 요약한 뒤 그 결과를 보여줄 수 있는 A2UI JSON 코드를 생성하세요.",
        tools=[types.Tool(function_declarations=tizen_decls)]
    )
    
    chat = client.aio.chats.create(model='gemini-2.5-flash', config=config)
    response = await chat.send_message(message)
    
    final_text = ""
    ui_code = ""
    
    # 함수 호출 확인
    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.function_call:
                # 실제로 기기 제어 수행
                res = execute_tizen_action(part.function_call.name, part.function_call.args)
                
                # 결과를 모델에게 다시 전달하여 최종 응답 및 A2UI 생성 유도
                final_res_obj = await chat.send_message(
                    types.Part.from_function_response(
                        name=part.function_call.name,
                        response=res
                    )
                )
                final_text = final_res_obj.text
                ui_code = extract_json(final_text) if "updateComponents" in final_text else ""
                break
    
    if not final_text:
        final_text = response.text
        
    return {"text": final_text, "ui_code": ui_code}

async def a2ui_draw_worker(message: str):
    """2단계 워커: 순수 A2UI 그리기 에이전트 (A2UI Agent)"""
    system_instruction = (
        "너는 A2UI(Agent-to-UI) v0.9 규격의 전문가야. "
        "사용자의 요청에 따라 창의적이고 프리미엄한 디자인의 UI를 A2UI JSON 형식으로 작성해. "
        "\n- 'version': 'v0.9' 포함 "
        "\n- 'createSurface', 'updateComponents' 메시지 리스트 구조 "
        "\n- 응답에는 오직 JSON 코드 블록만 포함하거나, JSON만 출력해."
    )
    
    response = await client.aio.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"사용자 요청: {message}\n이 요청에 맞는 멋진 A2UI 코드를 생성해줘.",
        config=types.GenerateContentConfig(system_instruction=system_instruction)
    )
    
    ui_code = extract_json(response.text)
    return {"text": "요청하신 디자인을 A2UI 규격으로 생성했습니다.", "ui_code": ui_code}

# --- 서버 환경 설정 ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, TIZEN_TOOLS_DATA
    
    # 도구 및 클라이언트 초기화
    TIZEN_TOOLS_DATA = discover_tizen_tools()
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    
    print(f"Server Ready. {len(TIZEN_TOOLS_DATA)} Tizen tools loaded.")
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    message: str

@app.get("/")
async def root():
    return {"status": "ok", "message": "Router-Worker Agent Server is running"}

@app.post("/connect")
async def connect_check():
    """시스템 상태 리포트 (기존 test.py 호환용)"""
    try:
        rev_check = subprocess.run(["sdb", "reverse", "--list"], capture_output=True, text=True)
        sdb_ok = "tcp:8080" in rev_check.stdout
    except:
        sdb_ok = False

    return {
        "sdb_reverse": "OK" if sdb_ok else "Disconnected",
        "llm_ready": "OK" if client else "Not Initialized",
        "tools_count": len(TIZEN_TOOLS_DATA),
        "tools_list": [t.get("name") for t in TIZEN_TOOLS_DATA],
        "can_chat": sdb_ok and client is not None,
        "message": "환영합니다! Router-Worker 시스템이 준비되었습니다." if sdb_ok else "SDB 연결을 확인해주세요."
    }

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        # 1단계: Router Agent를 통한 의도 파악
        print(f"\n[Router] Analyzing request: {request.message}")
        routing_result = await router_agent(request.message)
        tasks = routing_result.get("tasks", ["search"])
        print(f"[Router] Detected Tasks: {tasks}")
        
        # 2단계: 파싱된 Task에 따라 Worker 호출 (병렬 실행)
        worker_calls = []
        for task in tasks:
            if task == "draw_a2ui":
                worker_calls.append(a2ui_draw_worker(request.message))
            elif task == "device_control_a2ui":
                worker_calls.append(device_control_worker(request.message))
            elif task == "search":
                worker_calls.append(search_worker(request.message))
            elif task == "general_chat":
                worker_calls.append(chat_worker(request.message))
        
        # 워커 결과 취합
        if not worker_calls:
            worker_calls.append(chat_worker(request.message))
            
        results = await asyncio.gather(*(asyncio.wait_for(call, timeout=AI_RESPONSE_TIMEOUT) for call in worker_calls))
        
        # 결과 통합
        combined_text = []
        combined_ui = ""
        
        for res in results:
            if res.get("text"):
                combined_text.append(res["text"])
            if res.get("ui_code") and not combined_ui: # 첫 번째 발견된 UI 코드 사용
                combined_ui = res["ui_code"]
        
        return {
            "text": "\n\n".join(combined_text),
            "ui_code": combined_ui
        }

    except asyncio.TimeoutError:
        return {"text": "에이전트 응답 시간이 초과되었습니다. 다시 시도해 주세요.", "ui_code": ""}
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"text": f"서버 처리 중 오류 발생: {str(e)}", "ui_code": ""}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
