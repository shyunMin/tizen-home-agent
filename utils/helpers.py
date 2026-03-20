import re

def extract_json(text: str) -> str:
    """텍스트에서 JSON 블록 추출."""
    if not text:
        return ""
    match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    text = text.strip()
    if (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    ):
        return text
    obj_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if obj_match:
        return obj_match.group(1).strip()
    return ""
