from typing import List, Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class WorkerResult(TypedDict):
    """각 워커 노드의 결과."""
    task: str         # 워커 식별자
    text: str         # 워커가 생성한 텍스트 답변
    ui_code: str      # 워커가 생성한 A2UI JSON 문자열 (없으면 "")

class AgentState(TypedDict):
    # 대화 히스토리 (add_messages 리듀서로 누적)
    messages: Annotated[List[BaseMessage], add_messages]
    # Router가 결정한 task 목록
    tasks: List[str]
    # 각 워커 결과 누적 리스트
    worker_results: List[WorkerResult]
    # Reconstructor가 생성한 최종 답변
    final_text: str
    # Reconstructor가 생성한 최종 A2UI JSON 문자열
    ui_code: str
