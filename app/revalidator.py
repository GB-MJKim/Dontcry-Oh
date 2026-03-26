import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ParsedItem
from .pdf_assets import build_revalidation_pdf
from .settings import (
    OPENAI_API_KEY,
    OPENAI_FILE_EXPIRE_SECONDS,
    OPENAI_MODEL,
    OPENAI_TIMEOUT_SECONDS,
)

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

TEXT_ONLY_SYSTEM_PROMPT = """
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

PDF_ASSISTED_SYSTEM_PROMPT = """
너는 학교 홍보북 PDF의 가격 재검증 모델이다. 반드시 JSON만 반환한다.

규칙:
- 첨부된 PDF는 오류 재검증 대상 페이지에서 이미지를 제거하고 텍스트 레이어를 유지한 경량 PDF다.
- 입력 items의 excerpt_text 와 첨부 PDF를 함께 참고하되, anchor_title/product_name 에 해당하는 상품만 다시 확인한다.
- focus_fields 와 issues 는 어떤 필드가 틀렸거나 비어 있는지 알려주는 참고 정보다.
- expected_prices 는 기준 데이터일 뿐이며 PDF 또는 excerpt_text 에 근거가 없으면 절대 복사하지 않는다.
- product_name, master_name, anchor_title 는 타깃 상품 식별용 참고 정보다.
- excerpt_text 가 부분적으로 잘렸거나 줄바꿈이 어색할 수 있으므로, 필요하면 첨부 PDF에서 같은 상품 블록을 직접 확인한다.
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


def _extract_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def _page_number(item: ParsedItem, raw: Dict[str, Any]) -> Optional[int]:
    for value in (item.page, raw.get("page")):
        try:
            page_no = int(value)
        except (TypeError, ValueError):
            continue
        if page_no > 0:
            return page_no
    return None


def _get_revalidation_pdf_path(
    item: ParsedItem,
    raw: Dict[str, Any],
    cache: Dict[tuple[str, int], Optional[str]],
) -> Optional[str]:
    optimized_pdf = Path(str(raw.get("optimized_pdf") or "")).expanduser()
    page_no = _page_number(item, raw)
    if not optimized_pdf or not str(optimized_pdf).strip() or page_no is None:
        return None
    if not optimized_pdf.exists() or not optimized_pdf.is_file():
        return None

    cache_key = (str(optimized_pdf.resolve()), page_no)
    if cache_key in cache:
        return cache[cache_key]

    try:
        cache[cache_key] = build_revalidation_pdf(str(optimized_pdf), [page_no])
    except Exception:
        cache[cache_key] = None
    return cache[cache_key]


def _build_request_item(
    context: Dict[str, Any],
    revalidation_pdf_cache: Dict[tuple[str, int], Optional[str]],
) -> Dict[str, Any]:
    item: ParsedItem = context["item"]
    raw = item.raw if isinstance(item.raw, dict) else {}
    focus_fields = [field for field in context.get("focus_fields", []) if field in PRICE_FIELDS]
    attachment_path = _get_revalidation_pdf_path(item, raw, revalidation_pdf_cache)

    return {
        "id": str(context["index"]),
        "product_name": item.product_name,
        "master_name": context.get("product_name_master"),
        "anchor_title": raw.get("anchor_title"),
        "page": _page_number(item, raw),
        "pdf_spec": context.get("pdf_spec"),
        "master_spec": context.get("master_spec"),
        "focus_fields": focus_fields,
        "issues": list(context.get("problems", [])),
        "current_prices": _price_subset(context.get("pdf_prices", {}), focus_fields),
        "expected_prices": _price_subset(context.get("master_prices", {}), focus_fields),
        "excerpt_text": _focus_excerpt(str(raw.get("excerpt_text") or ""), focus_fields),
        "_attachment_path": attachment_path,
    }


def _get_openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않아 2차 AI 재검증을 수행할 수 없습니다.")
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 설치되지 않아 2차 AI 재검증을 수행할 수 없습니다.")
    return OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT_SECONDS)


def _request_payload(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload_items: List[Dict[str, Any]] = []
    for item in items:
        payload_items.append(
            {
                "id": item["id"],
                "product_name": item.get("product_name"),
                "master_name": item.get("master_name"),
                "anchor_title": item.get("anchor_title"),
                "page": item.get("page"),
                "pdf_spec": item.get("pdf_spec"),
                "master_spec": item.get("master_spec"),
                "focus_fields": list(item.get("focus_fields") or []),
                "issues": list(item.get("issues") or []),
                "current_prices": dict(item.get("current_prices") or {}),
                "expected_prices": dict(item.get("expected_prices") or {}),
                "excerpt_text": item.get("excerpt_text"),
            }
        )
    return payload_items


def _call_text_only_revalidation_batch(client, payload_items: List[Dict[str, Any]]) -> Dict[str, dict]:
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": TEXT_ONLY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": _request_payload(payload_items)}, ensure_ascii=False)},
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


def _call_pdf_assisted_revalidation_batch(
    client,
    payload_items: List[Dict[str, Any]],
    pdf_path: str,
) -> Dict[str, dict]:
    if not pdf_path or not Path(pdf_path).exists():
        raise RuntimeError("재검증용 PDF 파일을 찾지 못했습니다.")

    with open(pdf_path, "rb") as file_handle:
        uploaded = client.files.create(
            file=file_handle,
            purpose="user_data",
            expires_after={"anchor": "created_at", "seconds": OPENAI_FILE_EXPIRE_SECONDS},
        )

    user_text = (
        "첨부한 PDF는 오류 재검증 대상이 있는 페이지에서 이미지를 제거하고 텍스트 레이어를 유지한 경량 PDF입니다. "
        "아래 items JSON과 함께 참고해서 각 id의 focus_fields만 다시 확인하세요. "
        "expected_prices 는 참고용이므로 PDF나 excerpt_text 근거가 없으면 복사하지 마세요. "
        "JSON만 반환하세요.\n\n"
        + json.dumps({"items": _request_payload(payload_items)}, ensure_ascii=False)
    )

    response = client.responses.create(
        model=OPENAI_MODEL,
        temperature=0,
        text={"format": {"type": "json_object"}},
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": PDF_ASSISTED_SYSTEM_PROMPT}]},
            {
                "role": "user",
                "content": [
                    {"type": "input_file", "file_id": uploaded.id},
                    {"type": "input_text", "text": user_text},
                ],
            },
        ],
    )

    parsed = _extract_json(getattr(response, "output_text", "") or "{}")
    result_map: Dict[str, dict] = {}
    for item in parsed.get("items", []):
        item_id = _clean_text(item.get("id"))
        if item_id:
            result_map[item_id] = item
    return result_map


def _group_request_items(request_items: List[Dict[str, Any]]) -> List[tuple[Optional[str], List[Dict[str, Any]]]]:
    grouped: Dict[Optional[str], List[Dict[str, Any]]] = {}
    order: List[Optional[str]] = []
    for item in request_items:
        key = item.get("_attachment_path")
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    grouped_batches: List[tuple[Optional[str], List[Dict[str, Any]]]] = []
    for key in order:
        for batch in _chunk(grouped.get(key, []), REVALIDATION_BATCH_SIZE):
            grouped_batches.append((key, batch))
    return grouped_batches


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
    revalidation_pdf_cache: Dict[tuple[str, int], Optional[str]] = {}

    for context in candidates:
        request_item = _build_request_item(context, revalidation_pdf_cache)
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
            "input_mode": None,
            "pdf_used": bool(request_item.get("_attachment_path")),
            "fallback_used": False,
            "batch_error": None,
        }

    summary["attempted"] = len(request_items)
    if not request_items:
        return summary

    try:
        client = _get_openai_client()
        for attachment_path, batch in _group_request_items(request_items):
            input_mode = "text_only"
            batch_error: Optional[str] = None

            if attachment_path:
                try:
                    result_map = _call_pdf_assisted_revalidation_batch(client, batch, attachment_path)
                    input_mode = "pdf_plus_text"
                except Exception as exc:
                    batch_error = str(exc)
                    result_map = _call_text_only_revalidation_batch(client, batch)
                    input_mode = "text_only_fallback"
            else:
                result_map = _call_text_only_revalidation_batch(client, batch)

            for request_item in batch:
                context = context_map[request_item["id"]]
                item = context["item"]
                review = item.raw.get("second_pass_review", {})
                response_item = result_map.get(request_item["id"], {})
                review["input_mode"] = input_mode
                review["fallback_used"] = input_mode == "text_only_fallback"
                review["batch_error"] = batch_error
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
