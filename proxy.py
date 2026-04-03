import os
import subprocess
import json
import datetime
import time
import asyncio
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

# 1. 환경 변수 로드 (.env 파일이 있으면 로드함)
load_dotenv()

# 주요 환경 변수 설정 (기본값 설정 포함)
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
PROJECT_ID = os.getenv("VERTEX_AI_PROJECT", "shyun-gemini-project")
LOCATION = os.getenv("VERTEX_AI_LOCATION", "asia-northeast3")
PROXY_PORT = int(os.getenv("OLLAMA_PROXY_PORT", 11434))

if GOOGLE_CREDENTIALS:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDENTIALS

def get_device_serial() -> Optional[str]:
    try:
        res = subprocess.run(["sdb", "devices"], capture_output=True, text=True)
        for line in res.stdout.strip().split("\n")[1:]:
            if "device" in line:
                return line.split()[0]
    except Exception:
        pass
    return None

def setup_sdb_reverse_v2(port: int):
    serial = get_device_serial()
    print(f"[Lifespan] SDB Setup (Port {port}, Serial: {serial or 'Default'})")
    try:
        subprocess.run(["sdb", "reverse", "--remove", f"tcp:{port}"], check=False, capture_output=True)
        subprocess.run(["sdb", "forward", "--remove", f"tcp:{port}"], check=False, capture_output=True)
        cmd = ["sdb"]
        if serial: cmd.extend(["-s", serial])
        cmd.extend(["reverse", f"tcp:{port}", f"tcp:{port}"])
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[Lifespan] SDB Reverse established successfully.")
    except Exception as e:
        print(f"[Lifespan] SDB Error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_sdb_reverse_v2(PROXY_PORT)
    yield
    print("[Lifespan] Stopping Proxy...")

app = FastAPI(title="Ollama Proxy (Env Balanced)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def map_ollama_options_to_vertex(options):
    mapping = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "num_predict": "max_output_tokens", "stop": "stop_sequences"}
    vertex_kwargs = {}
    if not options: return vertex_kwargs
    for o_key, v_key in mapping.items():
        if o_key in options: vertex_kwargs[v_key] = options[o_key]
    return vertex_kwargs

def get_llm(model="gemini-2.5-flash", **kwargs):
    return ChatGoogleGenerativeAI(
        model=model, 
        vertexai=True, 
        project=PROJECT_ID, 
        location=LOCATION, 
        **kwargs
    )

@app.get("/")
async def root():
    return {"status": "success", "message": "Proxy is running"}

@app.post("/api/chat")
@app.post("/chat")
async def chat_endpoint(request: Request):
    try:
        body = await request.json()
        
        # 1. 요청 기록 (가장 최근 요청을 파일로 저장)
        try:
            with open("last_request.json", "w", encoding="utf-8") as f:
                json.dump(body, f, indent=2, ensure_ascii=False)
            print("[PC Proxy] Incoming request saved to 'last_request.json'")
        except Exception: pass
        
        # 2. 모델 설정 (요청값에 상관없이 gemini-2.5-flash 사용)
        model_name = "gemini-2.5-flash"

        main_msg = body.get("message", "") or body.get("prompt", "") or body.get("input", "")
        ollama_messages = body.get("messages", [])
        
        langchain_messages = []
        if isinstance(ollama_messages, list):
            i = 0
            while i < len(ollama_messages):
                m = ollama_messages[i]
                # 1) 정상적인 dict 형태 처리: {"role": "...", "content": "..."}
                if isinstance(m, dict):
                    role, content = m.get("role"), m.get("content", "")
                    tool_calls_raw = m.get("tool_calls", [])
                    if role == "system":
                        langchain_messages.append(SystemMessage(content=content))
                    elif role == "user":
                        langchain_messages.append(HumanMessage(content=content))
                    elif role == "assistant":
                        # 이전에 tool_calls를 반환한 assistant 메시지 복원
                        if tool_calls_raw:
                            lc_tool_calls = []
                            for tc in tool_calls_raw:
                                fn = tc.get("function", {})
                                args = fn.get("arguments", {})
                                if isinstance(args, str):
                                    try: args = json.loads(args)
                                    except: args = {}
                                fn_name = fn.get("name", "")
                                # id가 없으면 function name을 fallback ID로 사용
                                tc_id = tc.get("id") or fn_name
                                lc_tool_calls.append({
                                    "name": fn_name,
                                    "args": args,
                                    "id": tc_id,
                                    "type": "tool_call",
                                })
                            langchain_messages.append(AIMessage(content=content or "", tool_calls=lc_tool_calls))
                        else:
                            langchain_messages.append(AIMessage(content=content))
                    elif role == "tool":
                        # 클라이언트에서 보낸 도구 실행 결과
                        tool_call_id = m.get("tool_call_id", "")
                        
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        # ID 매칭 강화 (TizenClaw OllamaBackend는 ID를 안 보냄)
                        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                        if not tool_call_id:
                            # 1. 현재 Turn에서 아직 매칭되지 않은 tool_call을 찾음
                            # 직전의 assistant 메시지를 찾아서 tool_calls 리스트 확보
                            found_id = None
                            # 역순으로 assistant를 찾되, 이미 매칭된 tool은 스킵해야 함
                            # 간단한 전략: 바로 앞의 assistant를 찾고, 
                            # 그 뒤에 있는 tool 메시지들의 개수를 세서 현재 index를 알아냄
                            assistant_idx = -1
                            tool_count_after_assistant = 0
                            for j in range(len(langchain_messages) - 1, -1, -1):
                                if isinstance(langchain_messages[j], AIMessage) and langchain_messages[j].tool_calls:
                                    assistant_idx = j
                                    break
                                if isinstance(langchain_messages[j], ToolMessage):
                                    tool_count_after_assistant += 1
                            
                            if assistant_idx != -1:
                                tcs = langchain_messages[assistant_idx].tool_calls
                                if tool_count_after_assistant < len(tcs):
                                    found_id = tcs[tool_count_after_assistant].get("id")
                            
                            tool_call_id = found_id or "default_id"

                        langchain_messages.append(ToolMessage(content=str(content), tool_call_id=tool_call_id))

                    i += 1
                # 2) 클라이언트 버그로 인한 ["role", "system"], ["content", "..."] 쌍 처리
                elif isinstance(m, list) and len(m) == 2 and m[0] == "role" and i + 1 < len(ollama_messages):
                    next_m = ollama_messages[i+1]
                    if isinstance(next_m, list) and len(next_m) == 2 and next_m[0] == "content":
                        role, content = m[1], next_m[1]
                        if role == "system": langchain_messages.append(SystemMessage(content=content))
                        elif role == "user": langchain_messages.append(HumanMessage(content=content))
                        elif role == "assistant": langchain_messages.append(AIMessage(content=content))
                        i += 2
                    else:
                        i += 1
                elif isinstance(m, str):
                    langchain_messages.append(HumanMessage(content=m))
                    i += 1
                else:
                    i += 1
        
        if not langchain_messages and main_msg:
            langchain_messages.append(HumanMessage(content=str(main_msg)))

        if not langchain_messages:
            return JSONResponse(status_code=400, content={"status": "error", "message": "No content found in request"})

        # IN 로깅 (실시간 요청인 마지막 메시지만 출력)
        if langchain_messages:
            m = langchain_messages[-1]
            idx = len(langchain_messages) - 1
            if isinstance(m, SystemMessage):
                print(f"[PC Proxy] [IN] (SYSTEM): {m.content[:80]}...")
            elif isinstance(m, HumanMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                print(f"[PC Proxy] [IN] (USER): {content}")
            elif isinstance(m, ToolMessage):
                print(f"[PC Proxy] [IN] (TOOL id={m.tool_call_id!r}): {m.content}")
            elif isinstance(m, AIMessage):
                tc_names = [tc.get("name") for tc in (m.tool_calls or [])]
                if tc_names:
                    print(f"[PC Proxy] [IN] (ASSISTANT): [tool_calls={tc_names}]")
                else:
                    content = m.content if isinstance(m.content, str) else str(m.content)
                    print(f"[PC Proxy] [IN] (ASSISTANT): {content}")

        # 3. LLM 호출 및 도구 바인딩
        options = map_ollama_options_to_vertex(body.get("options", {}))
        print(f"[PC Proxy] Calling LLM model: {model_name}")
        llm = get_llm(model=model_name, **options)
        
        tools = body.get("tools", [])
        if tools:
            print(f"[PC Proxy] Binding {len(tools)} tools to LLM")
            try:
                llm = llm.bind_tools(tools)
            except Exception as te:
                print(f"[Warning] Failed to bind tools: {te}")
        
        response = await llm.ainvoke(langchain_messages)

        result = response.content
        # 응답이 리스트 형태(Content Blocks/Multimodal)라면 텍스트만 추출
        if isinstance(result, list):
            result = "".join([part.get("text", "") if isinstance(part, dict) else str(part) for part in result])

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Tool Call 처리: Gemini가 tool_calls를 반환한 경우
        # Ollama 프로토콜 형식으로 변환하여 클라이언트에 반환
        # 클라이언트가 도구 실행 후 tool role 메시지로 결과를 보내면
        # 다음 요청에서 최종 텍스트 응답을 받게 됨
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if not result and hasattr(response, "tool_calls") and response.tool_calls:
            ollama_tool_calls = []
            for tc in response.tool_calls:
                # TizenClaw는 arguments가 JSON Object인 것을 선호함 (Gemini 백엔드와 동일한 동작)
                args_obj = tc.get("args", {})
                if isinstance(args_obj, str):
                    try: args_obj = json.loads(args_obj)
                    except: pass
                
                ollama_tool_calls.append({
                    "id": tc.get("id", tc.get("name", "")),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": args_obj,
                    }
                })
            tool_names = [t["function"]["name"] for t in ollama_tool_calls if isinstance(t, dict)]
            print(f"[PC Proxy] [OUT]: (Tool Calls) {tool_names}")
            resp_data = {
                "status": "success",
                "success": True,
                "content": "",
                "text": "",
                "response": "",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": ollama_tool_calls,
                },
                "done": True,
                "done_reason": "tool_calls",
            }
        else:
            print(f"[PC Proxy] [OUT]: {result}")
            resp_data = {
                "status": "success",
                "success": True,
                "content": result,
                "text": result,
                "response": result,
                "message": {"role": "assistant", "content": result},
                "done": True,
                "done_reason": "stop",
            }

        json_str = json.dumps(resp_data, ensure_ascii=False) + "\n"
        content_bytes = json_str.encode("utf-8")
        
        return Response(
            content=content_bytes,
            media_type="application/json; charset=utf-8",
            headers={"Content-Length": str(len(content_bytes)), "Connection": "close"},
            status_code=200
        )
    except Exception as e:
        print(f"[Error] {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "success": False, "message": str(e)})

def kill_process_on_port(port: int):
    try:
        subprocess.run(["fuser", "-k", "-n", "tcp", str(port)], check=False, capture_output=True)
        subprocess.run(f"lsof -ti:{port} | xargs -r kill -9", shell=True, check=False)
        time.sleep(1)
    except Exception: pass

if __name__ == "__main__":
    kill_process_on_port(PROXY_PORT)
    print(f"Starting Proxy on port {PROXY_PORT}...")
    uvicorn.run("proxy:app", host="0.0.0.0", port=PROXY_PORT, reload=False)
