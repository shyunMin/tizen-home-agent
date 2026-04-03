# TizenClaw Web Search Skill Integration Guide

이 문서는 TizenClaw 에이전트에 실시간 웹 검색 기능을 추가하고, 발생한 기술적 문제들을 해결하는 전 과정을 정리한 가이드입니다.

---

## 1. 목표 (Objective)
TizenClaw의 `knowledge_retriever` 에이전트가 로컬 지식에 답변이 없을 경우, 실시간 웹 검색(Gemini Search Grounding)을 통해 최신 정보를 가져올 수 있도록 기능을 활성화합니다.

## 2. 주요 설정 파일 변경

### 2.1 API 키 및 엔진 설정 (`web_search_config.json`)
`/opt/usr/share/tizenclaw/config/web_search_config.json` 파일을 수정하여 검색 엔진의 기본값과 API 키를 설정합니다.

*   **Default Engine**: `gemini` (가장 안정적인 검색 그라운딩 기능 제공)
*   **Gemini API Key**: Google AI Studio에서 발급받은 API 키 입력
*   **Google Search ID (CX)**: Programmable Search Engine에서 발급받은 ID 입력

### 2.2 에이전트 역할 업데이트 (`agent_roles.json`)
에이전트가 도구를 호출하도록 `/opt/usr/share/tizenclaw/config/agent_roles.json`을 수정합니다.

*   **`knowledge_retriever`**: `execute_cli` 도구 추가
*   **System Prompt**: "로컬 지식이 없을 경우 `tizen-web-search-cli`를 사용하여 웹 검색을 수행하라"는 지침 추가

---

## 3. 트러블슈팅 및 해결 과정

### 3.1 Google Cloud 403 Forbidden 에러
기기 및 PC에서 `403 Forbidden` 에러가 발생한 경우 다음과 같이 조치했습니다.

1.  **API 활성화**: Google Cloud Console의 **Library** 메뉴에서 `Custom Search API`를 다시 활성화.
2.  **결제 연결(Billing Link)**: 새로 만든 프로젝트가 유료 또는 무료 체험판 결제 계정에 명시적으로 **연결(Link)**되어 있는지 확인. (결제 연결이 없으면 검색 API 호출이 거부됨)

### 3.2 바이너리 도구의 파싱 결함 (Shadow Fix)
기본 내장된 `tizen-web-search-cli` C++ 바이너리가 JSON 설정 파일을 읽는 과정에서 파싱 버그(공백/줄바꿈 인식 오류)가 있어 키값을 가져오지 못하는 문제가 발견되었습니다.

**[해결책]**: 바이너리를 빌드할 수 없는 환경이므로 다음과 같이 도구 기능을 재구축(Wrapper)했습니다.

1.  **쉘 스크립트 래퍼 (`tizen-web-search-cli.sh`)**:
    *   `grep`을 통해 원시 JSON 파일에서 API 키를 직접 추출.
    *   `curl`을 사용하여 Gemini API(`gemini-2.5-flash`)를 직접 호출.
2.  **파이썬 전용 파서 (`gemini_parser.py`)**:
    *   API의 대용량 응답 JSON을 안전하게 읽고, 에이전트가 이해할 수 있는 규격화된 포맷으로 변환.
3.  **바이너리 교체**:
    *   기존 바이너리를 백업하고, 완성된 래퍼 스크립트를 동일한 경로(`tizen-web-search-cli`)로 배치하여 에이전트의 동작 방성을 유지함.

---

## 4. 기기 배포 및 서비스 재시작
수정된 파일들을 `sdb`를 통해 기기에 전송하고 환경을 갱신합니다.

```bash
# 파일 배포
sdb push web_search_config.json /opt/usr/share/tizenclaw/config/
sdb push agent_roles.json /opt/usr/share/tizenclaw/config/
sdb push tizen-web-search-cli.sh /opt/usr/share/tizenclaw/tools/cli/tizen-web-search-cli/tizen-web-search-cli

# 실행 권한 부여 및 서비스 재시작
sdb shell "chmod +x /opt/usr/share/tizenclaw/tools/cli/tizen-web-search-cli/tizen-web-search-cli"
sdb shell "systemctl restart tizenclaw.service"
```

## 5. 결과 확인
에이전트에게 **"오늘 뉴스 검색해줘"**와 같은 명령을 내리면, Gemini Search Grounding을 통해 실시간 뉴스 요약 정보가 반환되는 것을 확인할 수 있습니다.

---
**주의사항**: 에이전트가 이전 대화의 실패 기록(Context Memory)에 매몰되어 결과를 부정하는 경우, 대화 세션을 초기화(Reset Session)하고 다시 시도하는 것이 좋습니다.
