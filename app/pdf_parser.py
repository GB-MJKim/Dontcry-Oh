import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz
from rapidfuzz import fuzz

from . import data_manager
from .models import ParsedItem, PriceSet
from .settings import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TIMEOUT_SECONDS, PDF_TEXT_SNIPPET_LIMIT, TEMP_DIR

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


SYSTEM_PROMPT = """
너는 학교 홍보북 PDF에서 상품 정보를 추출하는 검수 보조 모델이다.
반드시 JSON만 반환한다.
각 target은 이미 상품명이 주어져 있다. target의 excerpt_text 안에서만 찾아라.
추출 기준:
- ingredient_content: 원재료명 또는 함량 정보. % 수치가 보이면 포함한다. 없으면 null.
- spec_text: 규격 텍스트. 예: 1kg, 1kg(약 55개), 800g(40gX20개) 등.
- spec_price: 규격단가 숫자만. 없으면 null.
- kg_price: KG단가 숫자만. 없으면 null.
- unit_price: 개당단가 숫자만. 없으면 null.
- confidence: 0~1 실수.
주의:
- 숫자는 쉼표 없이 정수로 반환한다.
- 할인/행사 원가와 할인가가 같이 있으면 현재 표시된 실제 판매가를 우선한다.
- 불확실하면 추정하지 말고 null을 사용한다.
응답 형식:
{"items":[{"id":"...","ingredient_content":null,"spec_text":null,"spec_price":null,"kg_price":null,"unit_price":null,"confidence":0.0,"evidence":"짧은 근거"}]}
""".strip()


def _safe_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(round(float(value)))
        except Exception:
            return None
    s = str(value).strip()
    if not s or s.lower() in {"null", "none", "nan", "-"}:
        return None
    s = s.replace(",", "")
    digits = re.findall(r"\d+", s)
    if not digits:
        return None
    try:
        return int("".join(digits))
    except Exception:
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def _clip_text(text: str, limit: int = PDF_TEXT_SNIPPET_LIMIT) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit] + " …"


def prepare_pdf_for_ai(input_pdf_path: str) -> str:
    """
    AI에는 PDF 바이너리 대신 텍스트 레이어만 전달한다.
    별도로 최적화 복사본 PDF를 만들어 보관하지만, 실제 텍스트 추출은 원본 PDF에서 수행한다.
    일부 파일은 이미지/도형 제거 시 텍스트가 훼손될 수 있어, 여기서는 안전한 압축만 적용한다.
    """
    src = fitz.open(input_pdf_path)
    out_path = Path(TEMP_DIR) / f"{Path(input_pdf_path).stem}_optimized.pdf"
    src.save(str(out_path), garbage=4, deflate=True, clean=True)
    src.close()
    return str(out_path)


def _name_similarity(line: str, row: dict) -> float:
    qcands = data_manager.name_candidates(line)
    best = 0.0
    for qc in qcands:
        for rc in row.get("name_candidates", []):
            score = float(max(
                fuzz.WRatio(qc, rc),
                fuzz.token_sort_ratio(qc, rc),
                95.0 if qc and rc and min(len(qc), len(rc)) >= 6 and (qc in rc or rc in qc) else 0.0,
            ))
            best = max(best, score)
    return best


def _best_title_line(lines: List[str], catalog_rows: List[dict]) -> Tuple[str, Optional[dict], float]:
    best_line = ""
    best_row = None
    best_score = 0.0
    skip_tokens = [
        "보관", "알러지", "원재료", "조리", "규격단가", "개당", "오븐", "튀김", "콤비", "모드",
        "kg단가", "KG단가", "규격", "단가", "kg", "KG", "원산지", "함량", "대두", "밀:", "국산)",
    ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) < 3 or len(line) > 40:
            continue
        if line.endswith(".") or line.endswith("다.") or line.endswith("요."):
            continue
        if line in {"냉동", "냉장", "상온"}:
            continue
        if "%" in line:
            continue
        if "/" in line and "(" not in line:
            continue
        if line.count(",") >= 1:
            continue
        if any(token in line for token in skip_tokens):
            continue
        if re.search(r"\d{2,}", line) and "(" not in line:
            continue
        for row in catalog_rows:
            score = _name_similarity(line, row)
            if score > best_score:
                best_line = line
                best_row = row
                best_score = score
    return best_line, best_row, best_score


def _split_variant_names(name: str) -> List[str]:
    raw = name.strip()
    match = re.search(r"^(.*?)\((.*?)\)\s*$", raw)
    if not match:
        return [raw]
    base = match.group(1).strip()
    inner = match.group(2).strip()
    variants: List[str] = []

    if ":" in inner:
        left, right = inner.split(":", 1)
        left = left.strip()
        right_parts = [x.strip() for x in right.split("/") if x.strip()]
        for part in right_parts:
            variants.append(f"{base} ({left}:{part})")
            variants.append(f"{base} ({left}/{part})")
    else:
        parts = [x.strip() for x in inner.split("/") if x.strip()]
        if 1 < len(parts) <= 6:
            for part in parts:
                variants.append(f"{base} ({part})")

    if not variants:
        variants = [raw]

    dedup: List[str] = []
    seen = set()
    for item in variants:
        key = data_manager.normalize_name(item)
        if key and key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup or [raw]


def _extract_discount(text: str) -> Tuple[bool, Optional[int], Optional[str]]:
    if not re.search(r"(sale|할인|특가|행사)", text, flags=re.I):
        return False, None, None
    rate_match = re.search(r"(\d{1,2})\s*%", text)
    rate = int(rate_match.group(1)) if rate_match else None
    label = rate_match.group(0) if rate_match else "할인"
    return True, rate, label


def _extract_exclusion(text: str) -> Tuple[bool, Optional[str]]:
    for keyword in ["증정", "사은품", "세트상품", "세트", "덤"]:
        if keyword in text:
            return True, f"{keyword} 문구가 있어 검수 제외했습니다."
    return False, None


def _rule_based_spec(text: str) -> str:
    patterns = [
        r"\d+(?:\.\d+)?\s*(?:kg|g|ml|l)(?:\s*\([^\n]+?\))?(?:\s*/\s*\d+(?:\.\d+)?\s*(?:kg|g|ml|l)(?:\s*\([^\n]+?\))?)*",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(0).strip()
    return ""


def _rule_based_prices(text: str) -> PriceSet:
    spec_match = re.search(r"규격\s*단가\s*[:：]?\s*([\d,]+)", text, flags=re.I)
    kg_match = re.search(r"kg\s*단가\s*[:：]?\s*([\d,]+)", text, flags=re.I)
    unit_match = re.search(r"개당\s*[:：]?\s*([\d,]+)", text, flags=re.I)

    spec_price = _safe_int(spec_match.group(1)) if spec_match else None
    kg_price = _safe_int(kg_match.group(1)) if kg_match else None
    unit_price = _safe_int(unit_match.group(1)) if unit_match else None

    if spec_price is None or (kg_price is None and unit_price is None):
        big_numbers = []
        for m in re.finditer(r"(?<!\d)(\d{3,8}(?:,\d{3})*)(?!\d)", text):
            big_numbers.append(_safe_int(m.group(1)))
        big_numbers = [n for n in big_numbers if n is not None]
        if spec_price is None and len(big_numbers) >= 1:
            spec_price = big_numbers[0]
        if kg_price is None and len(big_numbers) >= 2:
            kg_price = big_numbers[1]
        if unit_price is None and len(big_numbers) >= 3:
            unit_price = big_numbers[2]

    return PriceSet(spec_price=spec_price, kg_price=kg_price, unit_price=unit_price)


def _collect_targets(pdf_path: str, catalog_rows: List[dict]) -> List[dict]:
    doc = fitz.open(pdf_path)
    targets: List[dict] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        raw_blocks = [b for b in page.get_text("blocks") if str(b[4]).strip()]

        anchors: List[dict] = []
        for block in raw_blocks:
            x0, y0, x1, y1, text, *_ = block
            lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
            if not lines:
                continue
            title_line, matched_row, score = _best_title_line(lines, catalog_rows)
            if not matched_row or score < 92:
                continue
            anchor = {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "line": title_line,
                "score": score,
                "column": 0 if float(x0) < (page_width / 2) else 1,
                "matched_row": matched_row,
            }
            overlap = False
            for prev in anchors:
                if prev["column"] == anchor["column"] and abs(prev["y0"] - anchor["y0"]) < 18:
                    overlap = True
                    if anchor["score"] > prev["score"]:
                        prev.update(anchor)
                    break
            if not overlap:
                anchors.append(anchor)

        anchors.sort(key=lambda x: (x["column"], x["y0"]))

        for anchor in anchors:
            same_column = [x for x in anchors if x["column"] == anchor["column"] and x["y0"] > anchor["y0"]]
            next_y = min([x["y0"] for x in same_column], default=page_height + 1)
            region_blocks = []
            for block in raw_blocks:
                x0, y0, x1, y1, text, *_ = block
                column = 0 if float(x0) < (page_width / 2) else 1
                if column != anchor["column"]:
                    continue
                if float(y0) < anchor["y0"] - 2 or float(y0) >= next_y - 2:
                    continue
                region_blocks.append((float(y0), float(x0), str(text).strip()))
            region_blocks.sort(key=lambda x: (x[0], x[1]))
            region_text = "\n".join(t for _, _, t in region_blocks if t)
            if not region_text:
                continue

            explicit_discount, discount_rate, discount_label = _extract_discount(region_text)
            excluded, exclusion_reason = _extract_exclusion(region_text)
            variant_names = _split_variant_names(anchor["line"])
            if len(variant_names) == 1:
                variant_names = [anchor["line"]]

            for variant_index, product_name in enumerate(variant_names, start=1):
                targets.append({
                    "id": f"p{page_index + 1}_{len(targets) + 1}",
                    "page": page_index + 1,
                    "product_name": product_name,
                    "anchor_title": anchor["line"],
                    "score": anchor["score"],
                    "excerpt_text": _clip_text(region_text),
                    "explicit_discount": explicit_discount,
                    "discount_rate": discount_rate,
                    "discount_label": discount_label,
                    "excluded": excluded,
                    "exclusion_reason": exclusion_reason,
                    "rule_spec": _rule_based_spec(region_text),
                    "rule_prices": _rule_based_prices(region_text),
                })
    doc.close()
    return targets


def _get_openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일에 API 키를 입력해 주세요.")
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 설치되지 않았습니다. pip install -r requirements.txt 를 먼저 실행해 주세요.")
    return OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT_SECONDS)


def _call_openai_batch(page_targets: List[dict]) -> Dict[str, dict]:
    client = _get_openai_client()
    payload = {
        "targets": [
            {
                "id": t["id"],
                "product_name": t["product_name"],
                "anchor_title": t["anchor_title"],
                "excerpt_text": t["excerpt_text"],
            }
            for t in page_targets
        ]
    }

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )

    content = response.choices[0].message.content if response.choices else "{}"
    parsed = json.loads(content or "{}")
    result_map: Dict[str, dict] = {}
    for item in parsed.get("items", []):
        item_id = str(item.get("id", "")).strip()
        if item_id:
            result_map[item_id] = item
    return result_map


def parse_pdf(pdf_path: str, catalog_rows: List[dict]) -> List[ParsedItem]:
    optimized_pdf_path = prepare_pdf_for_ai(pdf_path)
    targets = _collect_targets(pdf_path, catalog_rows)
    if not targets:
        return []

    ai_results: Dict[str, dict] = {}
    page_numbers = sorted({t["page"] for t in targets})
    for page_no in page_numbers:
        batch = [t for t in targets if t["page"] == page_no]
        try:
            ai_results.update(_call_openai_batch(batch))
        except Exception as exc:
            raise RuntimeError(f"OpenAI 상품 추출 실패 (page {page_no}): {exc}") from exc

    items: List[ParsedItem] = []
    for target in targets:
        ai = ai_results.get(target["id"], {})
        rule_prices: PriceSet = target["rule_prices"]

        spec_text = _clean_text(ai.get("spec_text") or target["rule_spec"] or "")
        ingredient_content = _clean_text(ai.get("ingredient_content") or "") or None
        spec_price = _safe_int(ai.get("spec_price"))
        kg_price = _safe_int(ai.get("kg_price"))
        unit_price = _safe_int(ai.get("unit_price"))

        if spec_price is None:
            spec_price = rule_prices.spec_price
        if kg_price is None:
            kg_price = rule_prices.kg_price
        if unit_price is None:
            unit_price = rule_prices.unit_price

        evidence_parts = [
            f"anchor={target['anchor_title']}",
            f"excerpt={target['excerpt_text'][:240]}",
        ]
        if ai.get("evidence"):
            evidence_parts.append(f"ai={_clean_text(str(ai.get('evidence')))}")

        if not any([spec_text, ingredient_content, spec_price is not None, kg_price is not None, unit_price is not None]):
            continue

        items.append(
            ParsedItem(
                page=target["page"],
                item_index=len(items) + 1,
                product_name=target["product_name"],
                spec_text=spec_text,
                prices=PriceSet(
                    spec_price=spec_price,
                    kg_price=kg_price,
                    unit_price=unit_price,
                ),
                explicit_discount=target["explicit_discount"],
                discount_rate=target["discount_rate"],
                discount_label=target["discount_label"],
                excluded=target["excluded"],
                exclusion_reason=target["exclusion_reason"],
                ingredient_content=ingredient_content,
                evidence_text=" | ".join(evidence_parts),
                raw={
                    "target_id": target["id"],
                    "optimized_pdf": optimized_pdf_path,
                    "ai": ai,
                },
            )
        )

    return items
