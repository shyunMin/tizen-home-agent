import requests
import json
import time
import sys
import subprocess
from typing import Optional

def get_device_serial() -> Optional[str]:
    """연결된 첫 번째 Tizen 기기 시리얼 반환"""
    try:
        res = subprocess.run(["sdb", "devices"], capture_output=True, text=True)
        for line in res.stdout.strip().split("\n")[1:]:
            if "device" in line:
                return line.split()[0]
    except Exception:
        pass
    return None

def setup_sdb_forward(port: int = 9090):
    """SDB Forward 설정 (PC:PORT -> Device:PORT)"""
    serial = get_device_serial()
    print(f"[SDB] Setup Forwarding for Port {port} (Serial: {serial or 'Default'})...")
    try:
        # 기존 규칙 제거 및 재설정
        subprocess.run(["sdb", "forward", "--remove", f"tcp:{port}"], check=False, capture_output=True)
        
        cmd = ["sdb"]
        if serial: cmd.extend(["-s", serial])
        cmd.extend(["forward", f"tcp:{port}", f"tcp:{port}"])
        
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[SDB] Forward established: PC:{port} -> Device:{port}")
    except Exception as e:
        print(f"[SDB] Warning: Cannot setup forwarding. Please check SDB: {e}")

def test_chat_loop():
    # SDB 포워딩 자동 실행
    PORT = 9090
    setup_sdb_forward(PORT)
    
    base_url = f"http://localhost:{PORT}"
    chat_url = f"{base_url}/api/chat"
    
    print("\n" + "=" * 50)
    print(f" Tizen Chat API Simulator (Port {PORT})")
    print(" (Type 'exit' or 'quit' to stop)")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n[나]: ")
            
            if user_input.lower() in ['exit', 'quit']:
                print("Simulation terminated.")
                break
                
            if not user_input.strip():
                continue

            # 새로운 메시지 포맷 적용
            payload = {
                "prompt": user_input,
                "session_id": "1234567890"
            }
            
            # print(f"DEBUG: [REQUEST] -> {chat_url}")
            start_time = time.time()
            
            response = requests.post(
                chat_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Connection": "close"
                },
                timeout=120
            )

            print(f"DEBUG: [RESPONSE] Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"\n[RAW RESPONSE]:")
                print(json.dumps(data, indent=2, ensure_ascii=False))
                print(f"(Response Time: {time.time() - start_time:.2f}s)")
            else:
                print(f"\n[Error]: Server returned {response.status_code}")
                print(response.text)

        except requests.exceptions.Timeout:
            print("\n[Timeout]: The response took too long (> 120s).")
        except requests.exceptions.ConnectionError:
            print(f"\n[Error]: Cannot connect to {base_url}. (Did you start the device app?)")
        except Exception as e:
            print(f"\n[Unexpected Error]: {e}")

if __name__ == "__main__":
    try:
        test_chat_loop()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(0)
