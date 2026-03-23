import json
import re
from typing import Optional
from openai import OpenAI
from .settings import OPENAI_API_KEY, OPENAI_MODEL
from .models import ParsedItem, PriceSet

SYSTEM_PROMPT = """너는 한국어 식품 홍보북 PDF에서 상품 정보를 뽑아내는 파서다.
반드시 JSON만 반환한다.
빨간색만으로 할인이라고 단정하지 말고 SALE/할인/%/특가 문구가 있을 때만 explicit_discount=true.
증정/사은품/세트상품은 excluded=true.
출력 스키마:
{
  "product_name": str,
  "spec_text": str,
  "spec_price": int|null,
  "kg_price": int|null,
  "unit_price": int|null,
  "discount_rate": int|null,
  "discount_label": str|null,
  "explicit_discount": bool,
  "excluded": bool,
  "exclusion_reason": str|null
}
"""

def maybe_refine_item_with_ai(item: ParsedItem) -> ParsedItem:
    if not OPENAI_API_KEY:
        return item
    if len(item.product_name) >= 3 and item.prices.spec_price:
        return item
    client = OpenAI(api_key=OPENAI_API_KEY)
    user_prompt = f"""다음 텍스트는 한 상품 카드에서 추출한 것이다.
텍스트:
{item.body_text}

현재 추정값:
상품명={item.product_name}
규격={item.spec_text}
규격단가={item.prices.spec_price}
KG단가={item.prices.kg_price}
개당단가={item.prices.unit_price}
할인배너={item.discount_label}
할인율={item.discount_rate}
explicit_discount={item.explicit_discount}
excluded={item.excluded}

JSON만 반환해."""
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            text={"format": {"type": "json_object"}},
        )
        text = getattr(resp, "output_text", None)
        if not text:
            return item
        data = json.loads(text)
        item.product_name = data.get("product_name") or item.product_name
        item.spec_text = data.get("spec_text") or item.spec_text
        item.prices = PriceSet(
            spec_price=data.get("spec_price") or item.prices.spec_price,
            kg_price=data.get("kg_price") or item.prices.kg_price,
            unit_price=data.get("unit_price") or item.prices.unit_price,
        )
        item.discount_rate = data.get("discount_rate", item.discount_rate)
        item.discount_label = data.get("discount_label") or item.discount_label
        item.explicit_discount = bool(data.get("explicit_discount", item.explicit_discount))
        item.excluded = bool(data.get("excluded", item.excluded))
        item.exclusion_reason = data.get("exclusion_reason") or item.exclusion_reason
    except Exception:
        return item
    return item
