import requests
import json
import sys

BASE_URL = "http://localhost:9090"

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

def validate_a2ui(ui_json_str):
    """
    A2UI v0.9 규격을 간단히 검증합니다.
    """
    try:
        data = json.loads(ui_json_str)
        if not isinstance(data, list):
            return False, "A2UI 응답은 메시지 리스트(Array) 형태여야 합니다."
        
        for msg in data:
            if msg.get("version") != "v0.9":
                return False, f"지원하지 않는 A2UI 버전입니다: {msg.get('version')}"
            
            # createSurface 또는 updateComponents 중 하나는 있어야 함
            if not any(k in msg for k in ["createSurface", "updateComponents"]):
                return False, "메시지에 createSurface 또는 updateComponents 필드가 누락되었습니다."
                
        return True, "✅ A2UI v0.9 규격 준수 확인"
    except json.JSONDecodeError:
        return False, "JSON 형식이 올바르지 않습니다."
    except Exception as e:
        return False, f"검증 중 오류 발생: {str(e)}"

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
            
            ui_code = data.get('ui_code')
            if ui_code:
                print("\n📱 생성된 A2UI 코드:")
                print("-"*50)
                print(ui_code)
                
                # A2UI 규격 검증 수행
                is_valid, v_msg = validate_a2ui(ui_code)
                if is_valid:
                    print(f"\n✨ {v_msg}")
                else:
                    print(f"\n⚠️ A2UI 규격 경고: {v_msg}")

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
