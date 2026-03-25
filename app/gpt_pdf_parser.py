import json
import re
from typing import List, Dict, Any
from openai import OpenAI
from .models import ParsedItem, PriceSet
from .settings import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_FILE_EXPIRE_SECONDS

SYSTEM_PROMPT = """너는 한국 식품 홍보북 PDF를 직접 읽고 상품 정보를 추출하는 검수 파서다.
반드시 JSON만 반환한다.
규칙:
1) PDF의 각 상품 카드/상품 블록을 읽어 상품별로 한 행씩 추출한다.
2) 상품명은 마케팅 문구/원재료 설명을 붙이지 말고 핵심 상품명만 적는다.
3) 가격은 보이는 값만 기록하고, 안 보이면 null.
4) 빨간색만으로 할인이라고 단정하지 말고 SALE, 할인, %, 특가 같은 명시적 문구가 있을 때만 explicit_discount=true.
5) 증정, 사은품, 무료, 세트상품이면 excluded=true.
6) 숫자는 정수만 반환하고 쉼표는 제거한다.
반환 형식:
{"items":[{"page":1,"item_index":1,"product_name":"구운 핫도그","spec_text":"600 g (약 30g×20개)","spec_price":25000,"kg_price":41670,"unit_price":1250,"explicit_discount":true,"discount_rate":20,"discount_label":"6월 SALE 20%","excluded":false,"exclusion_reason":null,"evidence_text":"핵심 근거 텍스트"}]}
"""

def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end+1]
    return json.loads(text)

def _to_int(v):
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    s = "".join(ch for ch in str(v) if ch.isdigit())
    return int(s) if s else None

def parse_pdf_with_gpt(pdf_path: str) -> List[ParsedItem]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(pdf_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="user_data", expires_after={"anchor": "created_at", "seconds": OPENAI_FILE_EXPIRE_SECONDS})
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_file", "file_id": uploaded.id}, {"type": "input_text", "text": "첨부한 PDF를 직접 읽고 모든 상품을 JSON으로 추출해 주세요. JSON만 반환하세요."}]},
        ],
    )
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError("모델 응답에서 텍스트를 읽지 못했습니다.")
    data = _extract_json(output_text)
    parsed = []
    for idx, item in enumerate(data.get("items", []), start=1):
        parsed.append(ParsedItem(page=int(item.get("page") or 1), item_index=int(item.get("item_index") or idx), product_name=str(item.get("product_name") or "").strip(), spec_text=str(item.get("spec_text") or "").strip(), prices=PriceSet(spec_price=_to_int(item.get("spec_price")), kg_price=_to_int(item.get("kg_price")), unit_price=_to_int(item.get("unit_price"))), explicit_discount=bool(item.get("explicit_discount", False)), discount_rate=_to_int(item.get("discount_rate")), discount_label=(str(item.get("discount_label")).strip() if item.get("discount_label") is not None else None), excluded=bool(item.get("excluded", False)), exclusion_reason=(str(item.get("exclusion_reason")).strip() if item.get("exclusion_reason") is not None else None), evidence_text=(str(item.get("evidence_text")).strip() if item.get("evidence_text") is not None else None), raw=item))
    return parsed
