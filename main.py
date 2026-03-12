import os
import subprocess
import uvicorn
import json
import google.generativeai as genai
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()

# Gemini 설정
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# 전역 변수
TIZEN_TOOLS_DATA = []
model = None

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
        # "name : "으로 섹션 분리
        sections = raw_output.split("name : ")
        
        for section in sections:
            if not section.strip() or "schema :" not in section:
                continue
            
            try:
                # 명칭 추출
                name = section.split("\n")[0].strip()
                # 스키마 JSON 추출
                schema_part = section.split("schema :")[1]
                # "name :" 이나 "test successful" 이전까지가 실제 JSON
                json_str = schema_part.split("name :")[0].split("test successful")[0].strip()
                
                action_schema = json.loads(json_str)
                actions.append(action_schema)
            except Exception as e:
                print(f"Error parsing tool section: {e}")
                
        return actions
    except Exception as e:
        print(f"Tool discovery failed: {e}")
        return []

def execute_tizen_action(name: str, arguments: dict):
    """모든 Tizen 액션을 실행하는 공용 핸들러"""
    payload = {
        "id": 1,
        "params": {
            "name": name,
            "arguments": arguments
        }
    }
    # 공백 없는 콤팩트한 JSON 생성
    json_data = json.dumps(payload, separators=(',', ':'))
    full_command = f"action-tool execute '{json_data}'"
    
    print(f"Executing: {full_command}")
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
            "action": name,
            "params": arguments
        }
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else e.stdout.strip()
        return {"status": "error", "message": f"SDB command failed: {error_msg}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global model, TIZEN_TOOLS_DATA
    
    # 1. SDB Reverse 초기화
    try:
        subprocess.run(["sdb", "reverse", "tcp:8080", "tcp:8080"], check=True)
    except: 
        print("SDB reverse setup skipped or failed.")

    # 2. 실시간 도구 로드
    TIZEN_TOOLS_DATA = discover_tizen_tools()
    
    # 3. Gemini Function Declaration 생성
    declarations = []
    
    # [GenUI] A2UI 규격의 UI 생성을 위한 도구 추가
    declarations.append(
        genai.types.FunctionDeclaration(
            name="render_ui",
            description="사용자가 요청한 화면이나 디자인을 A2UI(Agent-to-UI) v0.9 규격의 JSON으로 생성합니다.",
            parameters={
                "type": "object",
                "properties": {
                    "a2ui_json": {
                        "type": "string",
                        "description": "A2UI v0.9 규격에 맞춘 JSON 문자열 (createSurface, updateComponents 메시지 포함)"
                    }
                },
                "required": ["a2ui_json"]
            }
        )
    )

    for tool in TIZEN_TOOLS_DATA:
        # Gemini가 한글 설명도 잘 이해하도록 데이터 구성
        declarations.append(
            genai.types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", f"Tizen control action: {tool['name']}"),
                parameters=tool["inputSchema"]
            )
        )
    
    # 4. 모델 초기화
    system_instruction = (
        "당신은 유능하고 친절한 AI 어시스턴트입니다. "
        "사용자의 일반적인 질문이나 대화에는 자연스럽게 응답하세요. "
        "만약 사용자가 특정 화면, 레이아웃, 혹은 디자인(예: 비행기 예약 UI, 날씨 카드 등)을 그려달라고 하거나 보여달라고 하면 "
        "반드시 'render_ui' 도구를 사용하여 A2UI v0.9 규격의 JSON을 생성하여 제공하세요. "
        "\n\n[A2UI v0.9 JSON 가이드]\n"
        "- Root object는 'version': 'v0.9'를 포함해야 하며, 'createSurface'와 'updateComponents' 메시지가 포함된 리스트 형식으로 작성하세요.\n"
        "- 'createSurface': surfaceId, catalogId (https://a2ui.org/specification/v0_9/basic_catalog.json) 포함\n"
        "- 'updateComponents': surfaceId와 components 리스트 포함\n"
        "- 주요 컴포넌트: Card, Column, Row, Text, Icon, Button, TextField, Box, Divider 등\n"
        "- UI 구조: 'id': 'root' 인 컴포넌트가 반드시 존재해야 하며, children 이나 child 속성으로 다른 컴포넌트의 id를 참조하여 트리를 구성하세요.\n"
        "\nTizen 기기 제어 요청이 있을 경우에는 해당하는 Tizen 액션 도구를 사용하고, 수행 결과를 사용자에게 친절하게 한국어로 설명해 주세요."
    )
    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash', # 안정적인 버전으로 권장 (혹은 1.5-flash)
        tools=[genai.types.Tool(function_declarations=declarations)] if declarations else None,
        system_instruction=system_instruction
    )
    
    print(f"System Ready. {len(TIZEN_TOOLS_DATA)} Tizen tools integrated.")
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    message: str

class TargetMessage(BaseModel):
    device_id: str
    content: str

@app.get("/")
async def root():
    return {"message": "Tizen Home Agent Server (Dynamic Tools) is running"}

@app.post("/connect")
async def connect_check():
    """시스템 상태 리포트"""
    try:
        rev_check = subprocess.run(["sdb", "reverse", "--list"], capture_output=True, text=True)
        sdb_ok = "tcp:8080" in rev_check.stdout
    except:
        sdb_ok = False

    return {
        "sdb_reverse": "OK" if sdb_ok else "Disconnected",
        "llm_ready": "OK" if model else "Not Initialized",
        "tools_count": len(TIZEN_TOOLS_DATA),
        "tools_list": [t.get("name") for t in TIZEN_TOOLS_DATA],
        "can_chat": sdb_ok and model is not None,
        "message": "환영합니다! 모든 준비가 완료되었습니다." if sdb_ok else "SDB 연결을 확인해주세요."
    }

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        if not model:
            return {"text": "서버 모델이 아직 준비되지 않았습니다.", "ui_code": ""}
            
        chat = model.start_chat()
        response = chat.send_message(request.message)
        
        ui_code = ""
        text_response = ""
        
        # 1. 첫 응답에서 함수 호출이 있는지 확인 (안전한 접근)
        function_call = None
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    function_call = part.function_call
                    break
        
        if function_call:
            # 호출된 함수명과 인자값 추출
            args_dict = {k: v for k, v in function_call.args.items()}
            
            # [GenUI] render_ui 도구인 경우
            if function_call.name == "render_ui":
                ui_code = args_dict.get("a2ui_json", "").strip()
                text_response = "요청하신 UI 디자인을 A2UI 규격으로 생성했습니다."
            else:
                # Tizen 액션 실행
                result = execute_tizen_action(function_call.name, args_dict)
                
                if result["status"] == "error":
                    text_response = f"장치 제어 중 문제가 발생했습니다: {result['message']}"
                else:
                    # 결과 전달 후 최종 응답 받기
                    response = chat.send_message({
                        "role": "function",
                        "parts": [{
                            "function_response": {
                                "name": function_call.name,
                                "response": result
                            }
                        }]
                    })
                    
                    # 안전하게 최종 텍스트 추출
                    if response.candidates and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if hasattr(part, 'text') and part.text:
                                text_response = part.text
                                break
                    
                    if not text_response:
                        text_response = f"성공적으로 {function_call.name} 액션을 수행했습니다."
                    
                    # A2UI 규격의 JSON 생성
                    a2ui_messages = [
                        {
                            "version": "v0.9",
                            "createSurface": {
                                "surfaceId": "tizen_action_result",
                                "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json"
                            }
                        },
                        {
                            "version": "v0.9",
                            "updateComponents": {
                                "surfaceId": "tizen_action_result",
                                "components": [
                                    {"id": "root", "component": "Card", "child": "container"},
                                    {"id": "container", "component": "Row", "children": ["icon", "msg_col"], "align": "center"},
                                    {"id": "icon", "component": "Icon", "name": "settings_remote"},
                                    {"id": "msg_col", "component": "Column", "children": ["title", "args"]},
                                    {"id": "title", "component": "Text", "text": f"{function_call.name} 실행 완료", "variant": "body1"},
                                    {"id": "args", "component": "Text", "text": json.dumps(args_dict, ensure_ascii=False), "variant": "caption"}
                                ]
                            }
                        }
                    ]
                    ui_code = json.dumps(a2ui_messages, ensure_ascii=False, indent=2)
        else:
            # 함수 호출이 없는 경우 안전하게 텍스트 가져오기
            if response.candidates and response.candidates[0].content.parts:
                 for part in response.candidates[0].content.parts:
                     if hasattr(part, 'text') and part.text:
                         text_response = part.text
                         break
            
            if not text_response:
                text_response = "죄송합니다. 적절한 답변을 생성하지 못했습니다."
            
        return {"text": text_response, "ui_code": ui_code}
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"text": f"서버 오류: {str(e)}", "ui_code": ""}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
