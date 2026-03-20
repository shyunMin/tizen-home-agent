"""
Tizen Home Agent - entry point
==============================
LangGraph기반의 Router-Worker 구조로 리팩토링된 최종 진입점 파일입니다.
"""

import os
import json
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from typing import List, Dict, Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# LangChain / LangGraph Core
from langchain_core.messages import HumanMessage

# Project Modules
import config
from config import PORT, AI_RESPONSE_TIMEOUT, TIZEN_TOOLS_DATA
from graph.state import AgentState
from graph.builder import build_graph, get_mermaid_diagram
from utils.sdb_handler import (
    get_device_serial,
    setup_sdb_reverse,
    discover_tizen_tools,
    check_sdb_reverse,
)

load_dotenv()

# 컴파일된 그래프 (앱 시작 시 생성)
compiled_graph = None

# ---------------------------------------------------------------------------
# FastAPI 앱 정의 및 Lifecycle 관리
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. SDB 환경 설정 (역방향 포트 포워딩)
    setup_sdb_reverse()

    # 2. Tizen 도구 동적 수집
    tools = discover_tizen_tools()
    config.TIZEN_TOOLS_DATA.extend(tools)
    print(f"Server Ready. {len(config.TIZEN_TOOLS_DATA)} Tizen tools loaded.")

    # 3. LangGraph 그래프 컴파일
    global compiled_graph
    compiled_graph = build_graph()
    print("LangGraph graph compiled successfully.")

    yield

app = FastAPI(
    title="Tizen Home Agent",
    description="LangGraph 기반 Router-Worker 에이전트 (Refactored)",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 요청/응답 스키마
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    text: str
    ui_code: str

# ---------------------------------------------------------------------------
# API 엔드포인트
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "message": "Tizen Home Agent is running"}

@app.get("/graph/mermaid", response_class=JSONResponse)
async def get_mermaid():
    """그래프 구조를 Mermaid 형식으로 반환."""
    return {"mermaid": get_mermaid_diagram(compiled_graph)}

@app.get("/graph/ascii", response_class=JSONResponse)
async def get_ascii():
    """그래프 구조를 ASCII 형식으로 반환."""
    if compiled_graph is None:
        return {"ascii": "graph not initialized"}
    try:
        return {"ascii": compiled_graph.get_graph().draw_ascii()}
    except Exception as e:
        return {"ascii": f"Error: {str(e)}"}

@app.post("/connect")
async def connect_check():
    """시스템 연결 상태 확인 (SDB, LLM, Tools)."""
    serial = get_device_serial()
    sdb_ok = check_sdb_reverse()

    return {
        "sdb_reverse": "OK" if sdb_ok else "Disconnected",
        "llm_ready": "OK" if compiled_graph else "Not Initialized",
        "device_serial": serial or "Not Detected",
        "tools_count": len(config.TIZEN_TOOLS_DATA),
        "tools_list": [t.get("name") for t in config.TIZEN_TOOLS_DATA],
        "can_chat": compiled_graph is not None,
        "message": "시스템이 준비되었습니다." if sdb_ok else "SDB 연결을 확인하세요."
    }

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """메인 대화 엔드포인트 (LangGraph 실행)."""
    if compiled_graph is None:
        return JSONResponse(status_code=503, content={"text": "그래프가 초기화되지 않았습니다.", "ui_code": ""})

    initial_state: AgentState = {
        "messages": [HumanMessage(content=request.message)],
        "tasks": [],
        "worker_results": [],
        "final_text": "",
        "ui_code": "",
    }

    try:
        final_state = await asyncio.wait_for(
            compiled_graph.ainvoke(initial_state),
            timeout=AI_RESPONSE_TIMEOUT,
        )
        final_text = final_state.get("final_text", "")
        ui_code = final_state.get("ui_code", "")
        return {"text": final_text, "ui_code": ui_code}
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"text": "응답 시간이 초초과되었습니다.", "ui_code": ""})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"text": f"서버 오류: {str(e)}", "ui_code": ""})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
