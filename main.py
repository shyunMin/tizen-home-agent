import os
import subprocess
import uvicorn
import google.generativeai as genai
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드 (GOOGLE_API_KEY 포함)
load_dotenv()

# Gemini 설정
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

def control_wifi(enabled: bool):
    """
    Tizen 기기의 WiFi 상태를 제어합니다.
    
    Args:
        enabled: WiFi를 켤지(True) 끌지(False) 여부
    """
    import json
    command_val = "on" if enabled else "off"
    
    # 제공된 스키마에 맞춘 JSON 데이터 구성
    payload = {
        "id":1,
        "params": {
            "name": "homeWifi",
            "arguments":{
                "command": command_val
            }
        }
    }
    
    json_data = json.dumps(payload, separators=(',', ':'))
    
    try:
        # sdb shell 'action-tool execute '{json_data}'' 명령어 실행
        # 복합적인 쿼트 처리를 위해 리스트 형태로 전달
        full_command = f"action-tool execute '{json_data}'"
        result = subprocess.run(
            ["sdb", "shell", full_command],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        return {"status": "success", "output": result.stdout.strip(), "enabled": enabled}
    except subprocess.CalledProcessError as e:
        # 에러 발생 시 stderr가 비어있을 수 있으므로 stdout도 함께 확인
        error_msg = e.stderr.strip() if e.stderr else e.stdout.strip()
        return {"status": "error", "message": f"SDB command failed: {error_msg}"}
    except Exception as e:
        return {"status": "error", "message": f"Connection error: {str(e)}"}

# Gemini 모델 초기화 (도구 설정)
model = genai.GenerativeModel(
    model_name='gemini-2.5-flash',
    tools=[control_wifi]
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작 시 SDB Reverse 설정
    try:
        print("Executing: sdb reverse tcp:8080 tcp:8080")
        subprocess.run(["sdb", "reverse", "tcp:8080", "tcp:8080"], check=True)
    except Exception as e:
        print(f"Initial SDB setup failed: {e}")
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    message: str

class TargetMessage(BaseModel):
    device_id: str
    content: str

@app.get("/")
async def root():
    return {"message": "Tizen Home Agent Server with Gemini is running"}

@app.post("/connect")
async def connect_check():
    """
    클라이언트 연결 시 시스템 상태(SDB, LLM, 도구)를 체크합니다.
    """
    status_report = {
        "sdb_reverse": "Unknown",
        "llm_ready": "Unknown",
        "tools_ready": "Unknown",
        "message": "",
        "can_chat": False
    }
    
    # 1. SDB Reverse 체크
    try:
        result = subprocess.run(["sdb", "reverse", "--list"], capture_output=True, text=True, timeout=5)
        if "tcp:8080" in result.stdout:
            status_report["sdb_reverse"] = "OK"
        else:
            status_report["sdb_reverse"] = "Missing (tcp:8080 not found)"
    except Exception as e:
        status_report["sdb_reverse"] = f"Error: {str(e)}"

    # 2. LLM 및 도구 세팅 체크
    try:
        # 가벼운 테스트 메시지로 LLM 응답성 확인
        test_chat = model.start_chat()
        test_response = test_chat.send_message("hi", generation_config={"max_output_tokens": 5})
        if test_response and test_response.text:
            status_report["llm_ready"] = "OK"
            
            # 도구 세팅 확인 (모델 설정에 도구가 포함되어 있는지)
            if model._tools:
                status_report["tools_ready"] = "OK"
            else:
                status_report["tools_ready"] = "Warning: No tools configured"
        else:
            status_report["llm_ready"] = "No response"
    except Exception as e:
        status_report["llm_ready"] = f"Error: {str(e)}"

    # 최종 결과 판단
    if status_report["sdb_reverse"] == "OK" and status_report["llm_ready"] == "OK":
        status_report["can_chat"] = True
        status_report["message"] = "시스템이 준비되었습니다. 대화가 가능합니다."
    else:
        status_report["message"] = "시스템 준비 중 문제가 발견되었습니다. 상태를 확인하세요."

    return status_report

@app.post("/message")
async def receive_message(message: TargetMessage):
    print(f"Received from target: {message}")
    return {"status": "success", "received": message}

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        # Gemini 대화 시작
        chat = model.start_chat()
        response = chat.send_message(request.message)
        
        ui_code = ""
        text_response = ""
        
        # 첫 응답에서 함수 호출이 있는지 확인
        # (Flash 모델은 보통 함수 호출 시 텍스트 파트가 비어있을 수 있습니다)
        function_call = None
        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_call = part.function_call
                break
        
        # 1. 도구(함수 호출)가 필요한 경우
        if function_call:
            if function_call.name == "control_wifi":
                args = function_call.args
                enabled = args.get("enabled", False)
                
                # 실제 장치 제어 실행
                result = control_wifi(enabled)
                
                if result["status"] == "error":
                    text_response = f"장치 제어 중 에러가 발생했습니다: {result['message']}. SDB 연결을 확인해주세요."
                else:
                    # 함수 실행 결과를 모델에게 다시 전달하여 최종 텍스트 응답 생성
                    response = chat.send_message(
                        {
                            "role": "function",
                            "parts": [
                                {
                                    "function_response": {
                                        "name": "control_wifi",
                                        "response": result
                                    }
                                }
                            ]
                        }
                    )
                    text_response = response.text
                    
                    # GenUI: Flutter Dart 코드 생성
                    ui_code = f"""
Card(
  elevation: 4,
  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
  child: Padding(
    padding: const EdgeInsets.all(16.0),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Row(
              children: [
                Icon(
                  { 'Icons.wifi' if enabled else 'Icons.wifi_off' },
                  color: { 'Colors.blue' if enabled else 'Colors.grey' },
                  size: 30,
                ),
                SizedBox(width: 12),
                Text(
                  'WiFi 제어',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                ),
              ],
            ),
            Switch(
              value: { 'true' if enabled else 'false' },
              onChanged: (val) {{}},
              activeColor: Colors.blue,
            ),
          ],
        ),
        SizedBox(height: 10),
        Text(
          '{ 'WiFi가 성공적으로 켜졌습니다.' if enabled else 'WiFi가 성공적으로 꺼졌습니다.' }',
          style: TextStyle(color: Colors.grey[700]),
        ),
      ],
    ),
  ),
)
""".strip()
        
        # 2. 도구와 관련 없는 일반적인 요청인 경우
        else:
            text_response = response.text
        
        return {
            "text": text_response,
            "ui_code": ui_code
        }
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {
            "text": f"오류가 발생했습니다: {str(e)}",
            "ui_code": ""
        }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
