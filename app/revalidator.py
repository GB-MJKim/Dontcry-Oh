import json
import re
from typing import Any, Dict, List, Optional

from .models import ParsedItem
from .settings import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TIMEOUT_SECONDS

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


REVALIDATION_BATCH_SIZE = 6
MIN_REVALIDATION_CONFIDENCE = 0.55
PRICE_FIELDS = ("spec_price", "kg_price", "unit_price")
FIELD_KEYWORDS = {
    "spec_price": ["규격", "규격단가", "판매가", "현재가", "행사가", "할인가"],
    "kg_price": ["kg", "kg단가", "l", "l단가", "판매가", "현재가", "행사가", "할인가"],
    "unit_price": ["개당", "입당", "조각당", "장당", "판매가", "현재가", "행사가", "할인가"],
}
COMMON_KEYWORDS = ["할인", "sale", "행사", "현재가", "판매가", "규격", "kg", "l", "개당", "입당", "조각당", "장당"]

SYSTEM_PROMPT = """
너는 학교 홍보북 PDF의 가격 재검증 모델이다. 반드시 JSON만 반환한다.

규칙:
- 입력 items의 excerpt_text 안에서만 다시 확인한다.
- focus_fields 와 issues 는 어떤 필드가 틀렸거나 비어 있는지 알려주는 참고 정보다.
- expected_prices 는 기준 데이터일 뿐이며 excerpt_text 에 근거가 없으면 절대 복사하지 않는다.
- product_name, master_name, anchor_title 도 참고만 하고 실제 값은 excerpt_text 에 명시된 것만 사용한다.
- 가격은 현재 판매가를 우선한다. 정상가와 할인가가 함께 있으면 할인가를 반환한다.
- 규격단가=spec_price, KG/L 단가=kg_price, 개당/입당/조각당/장당 단가=unit_price.
- 레시피, 조리법, 배너, 행사문구, 다른 상품은 무시한다.
- 숫자는 쉼표 없는 정수만 반환한다. 근거가 약하면 null.
- focus_fields 에 없는 필드는 null 로 둔다.

응답 형식:
{"items":[{"id":"...","spec_price":null,"kg_price":null,"unit_price":null,"confidence":0.0,"evidence":"짧은 근거"}]}
""".strip()


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(round(float(value)))
        except Exception:
            return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"null", "none", "nan", "-"}:
        return None
    digits = re.findall(r"\d+", raw.replace(",", ""))
    if not digits:
        return None
    try:
        return int("".join(digits))
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _chunk(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _focus_excerpt(text: str, focus_fields: List[str], limit: int = 1400) -> str:
    lines = [_clean_text(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    if len(lines) <= 12:
        joined = "\n".join(lines)
        return joined if len(joined) <= limit else joined[:limit].rstrip() + "\n..."

    keywords = {keyword.lower() for keyword in COMMON_KEYWORDS}
    for field in focus_fields:
        for keyword in FIELD_KEYWORDS.get(field, []):
            keywords.add(keyword.lower())

    selected_indexes = set(range(min(6, len(lines))))
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            for near in range(max(0, idx - 1), min(len(lines), idx + 2)):
                selected_indexes.add(near)

    focused_lines = [lines[idx] for idx in sorted(selected_indexes)]
    joined = "\n".join(focused_lines)
    if len(joined) <= limit:
        return joined
    return joined[:limit].rstrip() + "\n..."


def _price_subset(values: Dict[str, Optional[int]], focus_fields: List[str]) -> Dict[str, Optional[int]]:
    return {field: values.get(field) for field in focus_fields if field in PRICE_FIELDS}


def _build_request_item(context: Dict[str, Any]) -> Dict[str, Any]:
    item: ParsedItem = context["item"]
    raw = item.raw if isinstance(item.raw, dict) else {}
    focus_fields = [field for field in context.get("focus_fields", []) if field in PRICE_FIELDS]
    return {
        "id": str(context["index"]),
        "product_name": item.product_name,
        "master_name": context.get("product_name_master"),
        "anchor_title": raw.get("anchor_title"),
        "pdf_spec": context.get("pdf_spec"),
        "master_spec": context.get("master_spec"),
        "focus_fields": focus_fields,
        "issues": list(context.get("problems", [])),
        "current_prices": _price_subset(context.get("pdf_prices", {}), focus_fields),
        "expected_prices": _price_subset(context.get("master_prices", {}), focus_fields),
        "excerpt_text": _focus_excerpt(str(raw.get("excerpt_text") or ""), focus_fields),
    }


def _get_openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않아 2차 AI 재검증을 수행할 수 없습니다.")
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 설치되지 않아 2차 AI 재검증을 수행할 수 없습니다.")
    return OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT_SECONDS)


def _call_revalidation_batch(client, payload_items: List[Dict[str, Any]]) -> Dict[str, dict]:
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": payload_items}, ensure_ascii=False)},
        ],
    )
    content = response.choices[0].message.content if response.choices else "{}"
    parsed = json.loads(content or "{}")
    result_map: Dict[str, dict] = {}
    for item in parsed.get("items", []):
        item_id = _clean_text(item.get("id"))
        if item_id:
            result_map[item_id] = item
    return result_map


def revalidate_error_items(items: List[ParsedItem], contexts: List[Dict[str, Any]], region: str) -> Dict[str, Any]:
    del items, region

    candidates = [context for context in contexts if context.get("can_revalidate")]
    summary = {
        "attempted": 0,
        "updated_items": 0,
        "updated_fields": 0,
        "error": None,
    }
    if not candidates:
        return summary

    request_items: List[Dict[str, Any]] = []
    context_map: Dict[str, Dict[str, Any]] = {}
    for context in candidates:
        request_item = _build_request_item(context)
        if not request_item["focus_fields"] or not request_item["excerpt_text"]:
            continue
        request_items.append(request_item)
        context_map[request_item["id"]] = context
        context["item"].raw["second_pass_review"] = {
            "attempted": True,
            "issues": list(context.get("problems", [])),
            "focus_fields": list(request_item["focus_fields"]),
            "applied_fields": [],
            "evidence": None,
            "confidence": None,
        }

    summary["attempted"] = len(request_items)
    if not request_items:
        return summary

    try:
        client = _get_openai_client()
        for batch in _chunk(request_items, REVALIDATION_BATCH_SIZE):
            result_map = _call_revalidation_batch(client, batch)
            for request_item in batch:
                context = context_map[request_item["id"]]
                item = context["item"]
                review = item.raw.get("second_pass_review", {})
                response_item = result_map.get(request_item["id"], {})
                review["confidence"] = _safe_float(response_item.get("confidence"))
                review["evidence"] = _clean_text(response_item.get("evidence") or "") or None

                applied_fields: List[str] = []
                if (review["confidence"] or 0.0) >= MIN_REVALIDATION_CONFIDENCE:
                    for field in request_item["focus_fields"]:
                        value = _safe_int(response_item.get(field))
                        if value is None:
                            continue
                        if getattr(item.prices, field) != value:
                            setattr(item.prices, field, value)
                            applied_fields.append(field)
                            summary["updated_fields"] += 1

                review["applied_fields"] = applied_fields
                item.raw["second_pass_review"] = review
                if applied_fields:
                    summary["updated_items"] += 1
    except Exception as exc:
        summary["error"] = str(exc)

    return summary
