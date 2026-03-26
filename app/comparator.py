from typing import Any, Dict, List, Optional

from . import data_manager
from .models import InspectionRow, ParsedItem

STATUS_NORMAL = "정상"
STATUS_ERROR = "오류"
STATUS_DISCOUNT = "할인적용"
STATUS_EXCLUDED = "제외"
STATUS_REVIEW = "확인필요"

FIELD_LABELS = {
    "spec_price": "규격단가",
    "kg_price": "KG/L단가",
    "unit_price": "개당단가",
    "spec_text": "규격",
}


def _apply_discount(price: Optional[int], rate: Optional[int]) -> Optional[int]:
    if price is None:
        return None
    if rate is None:
        return price
    return int(round(price * (100 - rate) / 100.0))


def _same(a: Optional[int], b: Optional[int], tol: int = 0) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _pdf_prices(item: ParsedItem) -> Dict[str, Optional[int]]:
    return {
        "spec_price": item.prices.spec_price,
        "kg_price": item.prices.kg_price,
        "unit_price": item.prices.unit_price,
    }


def _empty_prices() -> Dict[str, Optional[int]]:
    return {
        "spec_price": None,
        "kg_price": None,
        "unit_price": None,
    }


def _field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


def _problem_messages(
    pdf_prices: Dict[str, Optional[int]],
    expected_prices: Dict[str, Optional[int]],
    region: str,
    pdf_spec: str = "",
    master_spec: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    focus_fields: list[str] = []
    measure_label = data_manager.measure_price_label(pdf_spec, master_spec)

    if pdf_prices["spec_price"] is None:
        if expected_prices["spec_price"] is not None:
            problems.append("규격단가를 PDF에서 찾지 못했습니다.")
            focus_fields.append("spec_price")
    elif expected_prices["spec_price"] is not None and not _same(pdf_prices["spec_price"], expected_prices["spec_price"]):
        problems.append("규격단가가 기준 데이터와 다릅니다.")
        focus_fields.append("spec_price")

    if region == data_manager.REGION_SU:
        if pdf_prices["kg_price"] is None:
            if expected_prices["kg_price"] is not None:
                problems.append(f"{measure_label}를 PDF에서 찾지 못했습니다.")
                focus_fields.append("kg_price")
        elif expected_prices["kg_price"] is not None and not _same(pdf_prices["kg_price"], expected_prices["kg_price"]):
            problems.append(f"{measure_label}가 기준 데이터와 다릅니다.")
            focus_fields.append("kg_price")

    if pdf_prices["unit_price"] is None:
        if expected_prices["unit_price"] is not None:
            problems.append("개당단가를 PDF에서 찾지 못했습니다.")
            focus_fields.append("unit_price")
    elif expected_prices["unit_price"] is not None and not _same(pdf_prices["unit_price"], expected_prices["unit_price"]):
        problems.append("개당단가가 기준 데이터와 다릅니다.")
        focus_fields.append("unit_price")

    return problems, list(dict.fromkeys(focus_fields))


def _append_second_pass_notes(notes: list[str], item: ParsedItem) -> None:
    review = item.raw.get("second_pass_review") if isinstance(item.raw, dict) else None
    if not isinstance(review, dict) or not review.get("attempted"):
        return

    issues = [issue for issue in review.get("issues", []) if issue]
    focus_fields = [field for field in review.get("focus_fields", []) if field]
    applied_fields = [field for field in review.get("applied_fields", []) if field]
    confidence = review.get("confidence")
    evidence = review.get("evidence")
    input_mode = review.get("input_mode")
    batch_error = review.get("batch_error")

    if issues:
        notes.append("2차 AI 재검증 사유: " + " / ".join(issues))
    if focus_fields:
        notes.append("2차 AI 재검증 대상: " + ", ".join(_field_label(field) for field in focus_fields))
    if input_mode == "pdf_plus_text":
        notes.append("2차 AI 재검증 입력: excerpt_text + 이미지 제거 PDF 페이지")
    elif input_mode == "text_only_fallback":
        notes.append("2차 AI 재검증 입력: excerpt_text (PDF 보강 실패 후 폴백)")
    elif input_mode == "text_only":
        notes.append("2차 AI 재검증 입력: excerpt_text")
    if applied_fields:
        notes.append("2차 AI 재검증 반영: " + ", ".join(_field_label(field) for field in applied_fields))
    else:
        notes.append("2차 AI 재검증을 수행했지만 확신 있는 수정값을 찾지 못했습니다.")
    if isinstance(confidence, (int, float)):
        notes.append(f"2차 AI 신뢰도: {float(confidence):.2f}")
    if evidence:
        notes.append(f"2차 AI 근거 텍스트: {evidence}")
    if batch_error and input_mode == "text_only_fallback":
        notes.append(f"PDF 보강 실패 사유: {batch_error}")


def _build_context(index: int, item: ParsedItem, master_df, region: str) -> Dict[str, Any]:
    pdf_prices = _pdf_prices(item)
    notes: list[str] = []

    if item.excluded:
        notes.append(item.exclusion_reason or "검수 제외 항목입니다.")
        return {
            "index": index,
            "item": item,
            "status": STATUS_EXCLUDED,
            "product_name_master": None,
            "match_score": 0.0,
            "pdf_spec": item.spec_text,
            "master_spec": None,
            "pdf_prices": pdf_prices,
            "master_prices": _empty_prices(),
            "notes": notes,
            "problems": [],
            "focus_fields": [],
            "can_revalidate": False,
        }

    row, score = data_manager.find_best_match(
        master_df,
        query=item.product_name,
        spec=item.spec_text,
        prices=pdf_prices,
        region=region,
    )

    if not row:
        if item.ingredient_content:
            notes.append(f"원재료 함량: {item.ingredient_content}")
        if item.evidence_text:
            notes.append(f"AI 근거 텍스트: {item.evidence_text}")
        _append_second_pass_notes(notes, item)
        notes.insert(0, f"매칭 점수: {float(score or 0.0):.1f}")
        notes.insert(0, "기준 데이터에서 상품을 찾지 못했습니다.")
        return {
            "index": index,
            "item": item,
            "status": STATUS_ERROR,
            "product_name_master": None,
            "match_score": float(score or 0.0),
            "pdf_spec": item.spec_text,
            "master_spec": None,
            "pdf_prices": pdf_prices,
            "master_prices": _empty_prices(),
            "notes": notes,
            "problems": ["기준 데이터에서 상품을 찾지 못했습니다."],
            "focus_fields": [],
            "can_revalidate": False,
        }

    master = data_manager.extract_master_prices(row, region, master_df)
    expected_prices = {
        "spec_price": master["spec_price"],
        "kg_price": master["kg_price"],
        "unit_price": master["unit_price"],
    }

    status = STATUS_REVIEW
    if item.explicit_discount:
        if item.discount_rate is not None:
            notes.append("할인 문구가 명시되어 할인 상품으로 처리했습니다.")
            notes.append(f"할인율 {item.discount_rate}% 기준으로 예상 가격을 계산했습니다.")
            expected_prices["spec_price"] = _apply_discount(expected_prices["spec_price"], item.discount_rate)
            expected_prices["kg_price"] = _apply_discount(expected_prices["kg_price"], item.discount_rate)
            expected_prices["unit_price"] = _apply_discount(expected_prices["unit_price"], item.discount_rate)
            status = STATUS_DISCOUNT
        else:
            notes.append("할인율 수치가 없고 할인 문구만 있어 안내로만 표시합니다.")
            status = STATUS_DISCOUNT

    problems, focus_fields = _problem_messages(
        pdf_prices,
        expected_prices,
        region,
        pdf_spec=item.spec_text,
        master_spec=master["spec_text"],
    )
    notes.append(f"매칭 점수: {float(score):.1f}")
    if item.ingredient_content:
        notes.append(f"원재료 함량: {item.ingredient_content}")
    if item.evidence_text:
        notes.append(f"AI 근거 텍스트: {item.evidence_text}")
    _append_second_pass_notes(notes, item)

    if problems:
        status = STATUS_ERROR
        notes.extend(problems)
    elif status != STATUS_DISCOUNT:
        status = STATUS_NORMAL

    excerpt_text = item.raw.get("excerpt_text") if isinstance(item.raw, dict) else None
    return {
        "index": index,
        "item": item,
        "status": status,
        "product_name_master": master["product_name"],
        "match_score": float(score),
        "pdf_spec": item.spec_text,
        "master_spec": master["spec_text"],
        "pdf_prices": pdf_prices,
        "master_prices": expected_prices,
        "notes": notes,
        "problems": problems,
        "focus_fields": focus_fields,
        "can_revalidate": bool(problems and focus_fields and excerpt_text),
    }


def inspect_items(items: List[ParsedItem], master_df, region: str) -> List[Dict[str, Any]]:
    return [_build_context(index, item, master_df, region) for index, item in enumerate(items, start=1)]


def compare_items(items: List[ParsedItem], master_df, region: str) -> List[InspectionRow]:
    rows: List[InspectionRow] = []
    for context in inspect_items(items, master_df, region):
        rows.append(
            InspectionRow(
                context["index"],
                context["status"],
                context["item"].product_name,
                context["product_name_master"],
                context["match_score"],
                context["pdf_spec"],
                context["master_spec"],
                context["pdf_prices"],
                context["master_prices"],
                context["notes"],
            )
        )
    return rows
