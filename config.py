import os
from typing import List, Dict, Any, Optional, Literal, Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel, Field, ConfigDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# ---------------------------------------------------------------------------
# 전역 설정
# ---------------------------------------------------------------------------
PORT = 9090
AI_RESPONSE_TIMEOUT = 60  # 초

# 앱 시작 시 채워지는 전역 도구 목록 (Tizen SDB 도구)
# main.py의 lifespan에서 초기화됩니다.
TIZEN_TOOLS_DATA: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------
# A2UI v0.9 Pydantic 스키마
# ---------------------------------------------------------------------------

class A2uiComponent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    id: str = Field(description="컴포넌트 고유 ID (예: 'root', 'card-1')")
    component: Dict[str, Any] = Field(
        description="컴포넌트 타입별 데이터 (예: {'Text': {'text': '안녕'}}, {'Column': {'children': ['id1']}})"
    )

class A2uiLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(description="레이아웃 타입 (예: Vertical, Horizontal)")
    properties: Optional[Dict[str, Any]] = Field(default=None, description="레이아웃 속성")
    components: List[A2uiComponent] = Field(default_factory=list, description="컴포넌트 리스트")

class A2uiCreateSurface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surfaceId: str
    layout: A2uiLayout

class A2uiUpdateComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surfaceId: str
    components: List[A2uiComponent]

class A2uiMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    createSurface: Optional[A2uiCreateSurface] = None
    updateComponents: Optional[A2uiUpdateComponents] = None

class A2uiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(default="v0.9")
    messages: List[A2uiMessage]


# ---------------------------------------------------------------------------
# Structured Output: Router 결과
# ---------------------------------------------------------------------------

TaskType = Literal["general_chat", "search", "device_control_a2ui", "draw_a2ui"]

class RouterResult(BaseModel):
    """Router 노드의 구조화된 출력."""
    intent: Literal["simple", "complex"] = Field(
        description="단일 작업이면 'simple', 복합 작업이면 'complex'"
    )
    tasks: List[TaskType] = Field(
        description="수행할 작업 목록. 최소 하나 이상."
    )
