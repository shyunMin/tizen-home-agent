import struct
import time
import subprocess
import os

# 리눅스 입력 이벤트 설정값 (Standard Linux Input Constants)
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3

ABS_MT_POSITION_X = 53  # 멀티터치 X 좌표
ABS_MT_POSITION_Y = 54  # 멀티터치 Y 좌표
BTN_TOUCH = 330         # 터치 상태 (누름/뗌)
SYN_REPORT = 0

# Tizen 아키텍처별 이벤트 포맷
EVENT_FORMAT_64 = 'llHHi'  # 64-bit (24 bytes)
EVENT_FORMAT_32 = 'iiHHi'  # 32-bit (16 bytes)

def get_device_info():
    """연결된 SDB 기기 시리얼과 아키텍처(비트수)를 가져옵니다."""
    info = {"serial": None, "format": EVENT_FORMAT_64}
    try:
        res = subprocess.run(['sdb', 'devices'], capture_output=True, text=True, timeout=5)
        lines = res.stdout.strip().split('\n')
        if len(lines) > 1:
            info["serial"] = lines[1].split('\t')[0].strip()
            
            # 아키텍처 확인
            arch_res = subprocess.run(['sdb', 'shell', 'uname -m'], capture_output=True, text=True, timeout=5)
            arch = arch_res.stdout.strip()
            if arch in ['armv7l', 'i686', 'arm']:
                info["format"] = EVENT_FORMAT_32
                print(f"ℹ️ Detected 32-bit architecture ({arch})")
            else:
                info["format"] = EVENT_FORMAT_64
                print(f"ℹ️ Detected 64-bit architecture ({arch})")
    except Exception as e:
        print(f"⚠️ Error detecting device info: {e}")
    return info

def create_event_bin(fmt, type, code, value):
    now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    return struct.pack(fmt, sec, usec, type, code, value)

def tap(device_info, x, y):
    serial = device_info["serial"]
    fmt = device_info["format"]
    device_path = '/dev/input/event2'
    local_tmp = "/tmp/tap_events.bin"
    remote_tmp = "/tmp/tap_events.bin"
    
    try:
        # 1. 바이너리 시퀀스 생성 (Down + Up)
        binary_data = b''
        # Down
        binary_data += create_event_bin(fmt, EV_ABS, ABS_MT_POSITION_X, x)
        binary_data += create_event_bin(fmt, EV_ABS, ABS_MT_POSITION_Y, y)
        binary_data += create_event_bin(fmt, EV_KEY, BTN_TOUCH, 1)
        binary_data += create_event_bin(fmt, EV_SYN, SYN_REPORT, 0)
        
        # 2. 로컬 임시 파일 저장 후 Push (Piping issue 방지)
        with open(local_tmp, "wb") as f:
            f.write(binary_data)
        
        # Push to device
        push_cmd = ['sdb']
        if serial: push_cmd.extend(['-s', serial])
        push_cmd.extend(['push', local_tmp, remote_tmp])
        subprocess.run(push_cmd, capture_output=True, check=True)
        
        # Apply Down Events
        shell_cmd = ['sdb']
        if serial: shell_cmd.extend(['-s', serial])
        shell_cmd.extend(['shell', f'cat {remote_tmp} >> {device_path}'])
        subprocess.run(shell_cmd, capture_output=True, check=True)
        
        # 실제 클릭 느낌을 위해 대기
        time.sleep(0.05)
        
        # Up Event 생성 및 적용
        binary_up = b''
        binary_up += create_event_bin(fmt, EV_KEY, BTN_TOUCH, 0)
        binary_up += create_event_bin(fmt, EV_SYN, SYN_REPORT, 0)
        
        with open(local_tmp, "wb") as f:
            f.write(binary_up)
            
        subprocess.run(push_cmd, capture_output=True, check=True)
        subprocess.run(shell_cmd, capture_output=True, check=True)
        
        print(f"✅ Tapped at ({x}, {y}) via SDB (File-Push method)")
        
    except Exception as e:
        print(f"❌ Error during remote tap: {e}")

def main():
    device_info = get_device_info()
    DEVICE = '/dev/input/event2'
    
    if not device_info["serial"]:
        print("⚠️ Warning: No SDB device found. Please connect a Tizen device.")
    else:
        print(f"✅ Connected to Device: {device_info['serial']}")

    print("="*50)
    print(f"📱 Tizen Remote Input Event Tester (Target: {DEVICE})")
    print("   바이너리 Push & Cat 방식으로 입력을 전송합니다.")
    print("   종료하려면 'exit' 또는 'q'를 입력하세요.")
    print("="*50)

    while True:
        try:
            user_input = input("\n[입력] X Y 좌표 (예: 500 400): ").strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ['exit', 'quit', 'q', '종료']:
                print("👋 프로그램을 종료합니다.")
                break
            
            parts = user_input.split()
            if len(parts) != 2:
                print("⚠️ 올바른 형식으로 입력해주세요 (예: 500 400).")
                continue
                
            x = int(parts[0])
            y = int(parts[1])
            
            tap(device_info, x, y)
            
        except ValueError:
            print("⚠️ 숫자로만 입력해주세요.")
        except KeyboardInterrupt:
            print("\n👋 프로그램을 강제 종료합니다.")
            break
        except Exception as e:
            print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    main()
