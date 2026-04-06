import json
import subprocess
import requests
import sys
import os
from typing import Optional

def get_device_serial() -> Optional[str]:
    """연결된 첫 번째 Tizen 기기 시리얼 반환."""
    try:
        res = subprocess.run(["sdb", "devices"], capture_output=True, text=True)
        lines = res.stdout.strip().split("\n")
        if len(lines) <= 1:
            return None
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return None

def setup_sdb_forward(port: int = 7777) -> bool:
    """SDB 포트 포워딩 설정 (Host -> Device)."""
    serial = get_device_serial()
    if not serial:
        print("Error: No Tizen device connected via SDB.")
        return False
    
    try:
        cmd = ["sdb", "-s", serial, "forward", f"tcp:{port}", f"tcp:{port}"]
        print(f"Executing: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"SDB forward (tcp:{port} -> tcp:{port}) established successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"SDB forward setup failed: {e.stderr.decode()}")
        return False
    except Exception as e:
        print(f"Unexpected error during SDB forward: {e}")
        return False

def test_message_loop():
    """사용자로부터 메시지를 계속 입력받아 localhost:7777/message로 전송."""
    PORT = 7777
    if not setup_sdb_forward(PORT):
        return

    url = f"http://localhost:{PORT}/message"
    headers = {"Content-Type": "application/json"}

    print("\n" + "=" * 50)
    print(f" Tizen UI App Message Tester (Port {PORT})")
    print(" 메시지를 입력하면 기기의 UI 앱으로 전송됩니다.")
    print(" /로 시작하는 경로를 입력하면 해당 파일의 내용을 보냅니다.")
    print(" (Type 'exit' or 'quit' to stop)")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n[입력/경로]: ")
            
            if user_input.lower() in ['exit', 'quit']:
                print("Tester terminated.")
                break
                
            if not user_input.strip():
                continue

            message_text = user_input
            # /로 시작하는 경우 파일 탐색
            if user_input.startswith('/'):
                if os.path.exists(user_input) and os.path.isfile(user_input):
                    try:
                        with open(user_input, 'r', encoding='utf-8') as f:
                            message_text = f.read()
                        print(f" -> [파일 읽기 완료]: {user_input} ({len(message_text)} bytes)")
                    except Exception as e:
                        print(f" -> [오류] 파일을 읽을 수 없습니다: {e}")
                        continue
                else:
                    print(f" -> [경고] 파일이 존재하지 않거나 올바른 경로가 아닙니다: {user_input}")
                    continue

            payload = {"text": message_text}
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if response.status_code == 200:
                sent_preview = message_text[:50].replace('\n', ' ') + "..." if len(message_text) > 50 else message_text
                print(f" -> [성공] 전송됨: {sent_preview}")
            else:
                print(f" -> [실패] 서버 응답 코드: {response.status_code}")
                print(response.text)

        except requests.exceptions.ConnectionError:
            print(f"\n[오류]: {url}에 연결할 수 없습니다. (기기에서 UI 앱이 실행 중인지 확인하세요.)")
        except KeyboardInterrupt:
            print("\nAborted.")
            break
        except Exception as e:
            print(f"\n[예기치 못한 오류]: {e}")

if __name__ == "__main__":
    test_message_loop()
