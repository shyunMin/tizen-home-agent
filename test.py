import requests
import json
import sys

BASE_URL = "http://localhost:8080"

def check_connection():
    print(f"\n[1/2] 서버 연결 확인 중... ({BASE_URL}/connect)")
    try:
        response = requests.post(f"{BASE_URL}/connect", timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 서버 연결 성공!")
            print(f"   - SDB 상태: {data.get('sdb_reverse')}")
            print(f"   - LLM 상태: {data.get('llm_ready')}")
            print(f"   - 발견된 도구: {data.get('tools_count')}개")
            if data.get('tools_list'):
                print(f"   - 사용 가능 도구: {', '.join(data.get('tools_list')[:5])}...")
            print(f"   - 메시지: {data.get('message')}")
            return data.get('can_chat', False)
        else:
            print(f"❌ 서버 응답 오류 (Status: {response.status_code})")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. main.py가 실행 중인지 확인하세요.")
        return False
    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        return False

def send_chat(message):
    print(f"\n[2/2] 메시지 전송 중: \"{message}\"")
    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json={"message": message},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            print("\n" + "="*50)
            print("🤖 에이전트 응답:")
            print("-"*50)
            print(f"{data.get('text')}")
            
            if data.get('ui_code'):
                print("\n📱 생성된 UI 코드 (Flutter):")
                print("-"*50)
                print(data.get('ui_code'))
            print("="*50)
        else:
            print(f"❌ 채팅 오류 (Status: {response.status_code})")
            print(response.text)
    except Exception as e:
        print(f"❌ 전송 실패: {str(e)}")

if __name__ == "__main__":
    # 인자로 메시지를 받았는지 확인
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
    else:
        # 인자가 없으면 입력을 받음
        user_input = input("\n메시지를 입력하세요: ")

    if user_input.lower() in ['exit', 'quit', 'q']:
        sys.exit()

    if check_connection():
        send_chat(user_input)
    else:
        print("\n⚠️ 시스템이 준비되지 않아 메시지를 보낼 수 없습니다.")
