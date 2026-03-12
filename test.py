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
    if not check_connection():
        print("\n⚠️ 시스템이 준비되지 않아 대화를 시작할 수 없습니다.")
        sys.exit(1)

    print("\n" + "="*50)
    print("💬 Tizen Home Agent와 대화를 시작합니다.")
    print("   (종료하려면 'exit', 'quit', 또는 'q'를 입력하세요)")
    print("="*50)

    # 인자로 첫 메시지가 전달된 경우 처리
    if len(sys.argv) > 1:
        initial_msg = " ".join(sys.argv[1:])
        send_chat(initial_msg)

    # 지속적인 대화 루프
    while True:
        try:
            user_input = input("\n나 > ").strip()
            
            if not user_input:
                continue

            if user_input.lower() in ['exit', 'quit', 'q', 'ㅂㅂ', '종료']:
                print("\n👋 대화를 종료합니다. 감사합니다!")
                break

            send_chat(user_input)
            
        except KeyboardInterrupt:
            print("\n\n👋 프로그램을 강제 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 오류 발생: {e}")
