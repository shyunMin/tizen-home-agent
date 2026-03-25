import struct
import time
import subprocess
import sys

# 리눅스 입력 이벤트 설정값 (Standard Linux Input Constants)
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3

ABS_MT_POSITION_X = 53  # 멀티터치 X 좌표
ABS_MT_POSITION_Y = 54  # 멀티터치 Y 좌표
BTN_TOUCH = 330         # 터치 상태 (누름/뗌)
SYN_REPORT = 0

# Tizen 64bit 기준 format: 8바이트 long 2개, 2바이트 short 2개, 4바이트 int 1개
EVENT_FORMAT = 'llHHi'

def get_device_serial():
    """연결된 SDB 기기 시리얼 번호를 가져옵니다."""
    try:
        res = subprocess.run(['sdb', 'devices'], capture_output=True, text=True, timeout=5)
        lines = res.stdout.strip().split('\n')
        if len(lines) > 1:
            # 첫 번째 기기 시리얼 반환
            return lines[1].split('\t')[0].strip()
    except:
        pass
    return None

def create_event_bin(type, code, value):
    now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    return struct.pack(EVENT_FORMAT, sec, usec, type, code, value)

def tap(serial, x, y):
    device_path = '/dev/input/event2'
    try:
        # SDB를 통해 타겟 디바이스의 입력 장치로 바이너리를 직접 전달 (dd 활용)
        cmd = ['sdb']
        if serial:
            cmd.extend(['-s', serial])
        cmd.extend(['shell', f'dd of={device_path} bs=24'])
        
        # 탭 이벤트 시퀀스 생성 (Down)
        binary_data = b''
        binary_data += create_event_bin(EV_ABS, ABS_MT_POSITION_X, x)
        binary_data += create_event_bin(EV_ABS, ABS_MT_POSITION_Y, y)
        binary_data += create_event_bin(EV_KEY, BTN_TOUCH, 1)
        binary_data += create_event_bin(EV_SYN, SYN_REPORT, 0)
        
        # 프로세스 실행 및 데이터 전송
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.stdin:
            proc.stdin.write(binary_data)
            proc.stdin.flush()
            
            # 실제 클릭 느낌을 위해 짧게 대기 후 Up 이벤트 전송
            time.sleep(0.05)
            
            binary_up = b''
            binary_up += create_event_bin(EV_KEY, BTN_TOUCH, 0)
            binary_up += create_event_bin(EV_SYN, SYN_REPORT, 0)
            
            proc.stdin.write(binary_up)
            proc.stdin.flush()
            proc.stdin.close()
        
        proc.wait(timeout=5)
        print(f"✅ Tapped at ({x}, {y}) on {device_path} (via SDB)")
        
    except Exception as e:
        print(f"❌ Error during remote tap: {e}")

def main():
    SERIAL = get_device_serial()
    DEVICE = '/dev/input/event2'
    
    if not SERIAL:
        print("⚠️ Warning: No SDB device found. Please connect a Tizen device.")
    else:
        print(f"✅ Connected to Device: {SERIAL}")

    print("="*50)
    print(f"📱 Tizen Remote Input Event Tester (Target: {DEVICE})")
    print("   X와 Y 좌표를 입력하면 SDB를 통해 원격으로 클릭(Tap)합니다.")
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
            
            tap(SERIAL, x, y)
            
        except ValueError:
            print("⚠️ 숫자로만 입력해주세요.")
        except KeyboardInterrupt:
            print("\n👋 프로그램을 강제 종료합니다.")
            break
        except Exception as e:
            print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    main()
