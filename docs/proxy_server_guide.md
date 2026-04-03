# Ollama API Proxy (Vertex AI) 상세 가이드

`proxy.py`는 Tizen 디바이스의 C++ 클라이언트 및 Ollama 클라이언트 요청을 수신하여 Google Vertex AI(Gemini 2.5 Flash)로 결과물을 중계하는 프록시 서버입니다.

## 1. 통신 및 네트워크 아키텍처
현재 코드는 SDB(Smart Development Bridge)를 사용하여 다음과 같이 네트워크를 구성합니다.

- **포트 단일화**: 모든 통신은 포트 **`11434`**를 통해 이루어집니다.
- **SDB Reverse (Device -> PC)**: 
  - 디바이스의 11434 포트 요청을 PC의 프록시 서버로 전달합니다.
  - 서버 구동 시 `lifespan` 이벤트를 통해 자동으로 설정되어 통신 안정성을 보장합니다.
- **포트 간섭 방지**: 11434 포트에 대한 `SDB Forward` 규칙은 서버 기동을 방해하므로 실행 시 자동으로 감지하여 제거합니다.

## 2. 입출력 규격 (API 호환성)
프록시는 다양한 클라이언트의 요청 방식을 수용할 수 있도록 다중 파싱 로직이 적용되어 있습니다.

### 요청 수신 (Request Body)
- **Ollama 스타일**: `messages` 리스트 형태의 질의 수용
- **Main.py/C++ 스타일**: 단일 필드인 `message` 또는 `prompt` 형식의 질의 수용
- **유연성**: 메시지 요소가 문자열(`str`)이거나 리스트(`list`)인 경우를 모두 방어적으로 처리합니다. 특히 C++ 클라이언트에서 발생하는 특수 포맷을 자동 감지하여 결합합니다.

### 응답 전송 (Response Body)
C++ 클라이언트와 Ollama 표준을 위해 다음과 같은 필드를 응답 본문에 포함합니다.
- **`content`**: C++ 클라이언트 파싱용 (최상위 필드)
- **`text`**: 기존 메인 에이전트(`main.py`) 호환용
- **`message.content`**: Ollama Chat API 규격 준수
- **`response`**: Ollama Generate API 규격 준수

## 3. Google Cloud 인증 및 서비스 계정 키 생성
프록시 서버가 Vertex AI(Gemini)에 접근하기 위해서는 서비스 계정 키 파일이 필요합니다.

### 키 파일 생성 단계:
1. **Google Cloud Console**([console.cloud.google.com](https://console.cloud.google.com))에 접속하여 프로젝트를 선택합니다.
2. **[IAM 및 관리자] > [서비스 계정]** 메뉴로 이동합니다.
3. **[+ 서비스 계정 만들기]** 버튼을 클릭하여 새로운 계정을 생성합니다.
4. **[사용자 권한 부여]** 단계에서 **`Vertex AI 사용자`** 역할을 할당합니다.
5. 생성된 서비스 계정의 **[키]** 탭으로 이동하여 **[키 추가] > [새 키 만들기]**를 선택합니다.
6. 키 유형을 **JSON**으로 선택하고 만들기를 누르면 파일이 다운로드됩니다.
7. 다운로드된 파일을 PC의 안전한 경로에 저장한 뒤, `.env` 파일의 `GOOGLE_APPLICATION_CREDENTIALS` 경로를 해당 파일의 절대 경로로 수정합니다.

---
*최종 업데이트: 2026-04-01*
