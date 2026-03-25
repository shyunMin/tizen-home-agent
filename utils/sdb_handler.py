import json
import subprocess
import re
from typing import List, Dict, Any, Optional
from config import PORT

def get_device_serial() -> Optional[str]:
    """연결된 첫 번째 Tizen 기기 시리얼 반환."""
    try:
        res = subprocess.run(["sdb", "devices"], capture_output=True, text=True)
        for line in res.stdout.strip().split("\n")[1:]:
            if "device" in line:
                return line.split()[0]
    except Exception:
        pass
    return None

def setup_sdb_reverse() -> bool:
    """SDB 역방향 포트 포워딩 설정."""
    serial = get_device_serial()
    try:
        cmd = ["sdb"]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["reverse", f"tcp:{PORT}", f"tcp:{PORT}"])
        print(f"Setting up SDB reverse ({serial or 'Default'}) on port {PORT}...")
        subprocess.run(cmd, check=True, timeout=5, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        if b"already" in e.stderr:
            return True
        print(f"SDB reverse setup failed: {e.stderr.decode()}")
    except Exception as e:
        print(f"SDB reverse unexpected error: {e}")
    return False

def discover_tizen_tools() -> List[Dict[str, Any]]:
    """SDB를 통해 Tizen 기기에서 사용 가능한 액션 목록 수집."""
    serial = get_device_serial()
    cmd = ["sdb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", "action-tool", "list-actions"])
    print(f"Discovering Tizen tools via SDB ({serial or 'Default'})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        actions: List[Dict[str, Any]] = []
        sections = result.stdout.split("\nname : ")
        for section in sections:
            if section.startswith("name : "):
                section = section[7:]
            if "schema :" not in section:
                continue
            try:
                lines = section.split("\n")
                name = lines[0].strip()   # noqa: F841 (name embedded in schema)
                schema_content = section.split("schema :")[1].strip()
                json_str = schema_content.split("... test successful")[0].strip()
                last_brace = json_str.rfind("}")
                if last_brace != -1:
                    json_str = json_str[: last_brace + 1]
                action_schema = json.loads(json_str)
                actions.append(action_schema)
            except Exception as e:
                print(f"Error parsing tool section: {e}")
        return actions
    except Exception as e:
        print(f"Tool discovery failed: {e}")
        return []

def execute_tizen_action(name: str, arguments: dict) -> Dict[str, Any]:
    """Tizen 기기에 액션을 전달하고 결과 반환."""
    payload = {"id": 1, "params": {"name": name, "arguments": arguments}}
    json_data = json.dumps(payload, separators=(",", ":"))
    full_command = f"action-tool execute '{json_data}'"
    print(f"[sdb_handler] Executing SDB action: {full_command}")
    serial = get_device_serial()
    cmd = ["sdb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", full_command])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        return {"status": "success", "output": result.stdout.strip(), "action": name}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def check_sdb_reverse() -> bool:
    """SDB 역방향 포트 포워딩 상태 확인 및 필요시 재설정."""
    serial = get_device_serial()
    rev_check_cmd = ["sdb"]
    if serial:
        rev_check_cmd.extend(["-s", serial])
    rev_check_cmd.extend(["reverse", "--list"])
    try:
        rev_check = subprocess.run(rev_check_cmd, capture_output=True, text=True)
        sdb_ok = f"tcp:{PORT}" in rev_check.stdout
        if not sdb_ok and serial:
            sdb_ok = setup_sdb_reverse()
        return sdb_ok
    except Exception:
        return False

def get_screen_resolution() -> Dict[str, int]:
    """SDB를 통해 Tizen 기기의 화면 해상도를 추출합니다."""
    serial = get_device_serial()
    cmd = ["sdb"]
    if serial:
        cmd.extend(["-s", serial])
    # winfo -screen_info 명령어 실행
    cmd.extend(["shell", "winfo -screen_info"])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # 출력 예시: "... ( 1280 x 720) ..."
            match = re.search(r"\(\s*(\d+)\s*x\s*(\d+)\s*\)", result.stdout)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                return {"width": w, "height": h}
    except Exception as e:
        print(f"[sdb_handler] Error getting screen info: {e}")

    return {"width": 1280, "height": 720}  # 기본값 반환
