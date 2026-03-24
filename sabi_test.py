import requests
import json
import os

def request_code_generation(user_prompt):
    """
    SraiSys API 서버에 자연어 기반 코드 생성 요청을 보냅니다.
    """
    url = "http://sabi.sraisys.com/v1/code/complete"
    headers = {"Content-Type": "application/json"}
    
    # API 서버의 사양에 따라 'prompt' 키 이름을 확인해 주세요.
    payload = {"prompt": user_prompt}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}

def main():
    print("=== Tizen App Builder API Test Shell ===")
    print("종료하려면 'exit' 또는 'quit'를 입력하세요.\n")

    while True:
        # 1. 사용자 입력 받기
        user_input = input("나> ").strip()

        # 종료 조건 확인
        if user_input.lower() in ['exit', 'quit']:
            print("테스트를 종료합니다.")
            break
        
        if not user_input:
            continue

        print("... 서버 응답 대기 중 ...")

        # 2. API 호출
        result = request_code_generation(user_input)

        # 3. 응답 표시
        print("-" * 40)
        if "error" in result:
            print(f"❌ 오류 발생: {result['error']}")
        else:
            # 전체 응답을 보기 좋게 출력
            print("✅ 서버 응답:")
            print(json.dumps(result, indent=4, ensure_ascii=False))
            
            # 파일 주소가 포함되어 있다면 하이라이트 (Key가 'file_url'인 경우 가정)
            file_url = result.get("file_url")
            if file_url:
                print(f"\n🔗 생성된 파일 주소: {file_url}")
        print("-" * 40 + "\n")

if __name__ == "__main__":
    main()