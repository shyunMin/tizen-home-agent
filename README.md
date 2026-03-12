# Tizen Home Agent with Gemini 2.5 Flash

Tizen 기기를 효율적으로 제어하기 위한 인텔리전트 에이전트 서버입니다. FastAPI와 Gemini 2.5 Flash의 Function Calling 기능을 사용하여 자연어로 Tizen 기기를 제어하고, 결과를 Flutter UI 코드로 응답받을 수 있습니다.

## 주요 기능
- **자연어 기기 제어**: "와이파이 켜줘"와 같은 일상적인 명령어로 Tizen 기기 제어
- **Tizen WiFi 제어**: `action-tool`을 사용하여 실제 Tizen 기기의 WiFi 상태 변경
- **GenUI 응답**: 기기 제어 성공 시 상태를 시각적으로 보여주는 Flutter(Dart) 위젯 코드 반환
- **SDB 자동화**: 서버 시작 시 `sdb reverse` 명령어를 자동으로 실행하여 통신 환경 설정

## 요구 사항
- Ubuntu 24.04 (또는 호환 리눅스 환경)
- Python 3.12+
- SDB (Tizen Studio 또는 Smart Development Bridge) 설치 및 환경 변수 설정
- Tizen 기기 (SDB를 통해 연결된 상태)
- Google Gemini API Key

## 설치 및 설정

### 1. 가상환경 구축 및 의존성 설치
```bash
# 가상환경 생성
python3 -m venv venv

# 가상환경 활성화
source venv/bin/activate

# 필수 라이브러리 설치
pip install -r requirements.txt
```

### 2. 환경 변수 설정
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 본인의 API 키를 입력합니다.
```text
GOOGLE_API_KEY=your_gemini_api_key_here
```

## 실행 방법

```bash
python main.py
```
서버는 기본적으로 `http://0.0.0.0:8080`에서 실행됩니다.

## API 사용법

### 1. 채팅 및 제어 엔드포인트 (`/chat`)
자연어를 통해 기기를 제어하거나 대화를 나눕니다.
- **Method**: `POST`
- **Body**: `{"message": "와이파이 꺼줘"}`
- **Response**:
  - `text`: Gemini의 답변 메시지
  - `ui_code`: (제어 시) Flutter UI 카드 위젯 코드

### 2. 기기 메시지 수신 엔드포인트 (`/message`)
Tizen 기기에서 직접 상태 정보를 보낼 때 사용합니다.
- **Method**: `POST`
- **Body**: `{"device_id": "TIZEN-001", "content": "Status update..."}`

## 라이선스
MIT License
