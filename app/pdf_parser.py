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
너는 학교 홍보북 PDF의 상품 카드만 읽는 추출 모델이다. 반드시 JSON만 반환한다.
입력은 targets 배열이며 각 target마다 items 1개를 반드시 반환한다.

규칙:
- 각 target은 id, product_name, anchor_title, excerpt_text를 가진다.
- excerpt_text 안에서 target.product_name 또는 anchor_title에 해당하는 상품만 추출한다.
- excerpt_text 안에 다른 상품, 레시피, 행사배너, 포스터 안내, 홍보문구, 조리방법, D-7, NEW, SALE 문구가 섞여 있어도 target 상품과 무관하면 무시한다.
- target 상품이 실제로 없거나 근거가 약하면 값을 추정하지 말고 null로 두고 confidence=0으로 반환한다.
- ingredient_content: 원재료/함량/% 정보만. 레시피 재료표는 제외한다.
- spec_text: 실제 판매 규격만. 여러 SKU/형태가 같이 있으면 base spec_text는 null로 두고 variants에 옵션별로 분리한다.
- spec_price: 규격단가의 현재 판매가.
- kg_price: kg단가 또는 L단가의 현재 판매가.
- unit_price: 개당/장당/조각당/팩당 등 개별 단가의 현재 판매가.
- 정상가와 할인가가 함께 보이면 spec_price/kg_price/unit_price에는 할인 적용 후 현재 판매가를 넣는다.
- 약 155조각, 20개입, 1인분, 알러지 숫자, 조리 온도/시간, 행사 D-day는 가격으로 읽지 않는다.
- 숫자는 쉼표 없는 정수만 쓴다. 불확실하면 null.

variants 규칙:
- 하나의 상품 카드에 2개 이상 판매 옵션이 있으면 variants 배열을 사용한다.
- 예: 600g/1kg, 통/슬라이스, 판/슬라이스/채.
- variants의 각 원소는 {"product_name":str|null,"ingredient_content":str|null,"spec_text":str|null,"spec_price":int|null,"kg_price":int|null,"unit_price":int|null,"evidence":str} 형식이다.
- variants를 쓰는 경우 base spec_text/spec_price/kg_price/unit_price는 null이어도 된다.

응답 형식:
{"items":[{"id":"...","ingredient_content":null,"spec_text":null,"spec_price":null,"kg_price":null,"unit_price":null,"confidence":0.0,"evidence":"짧은 근거","variants":[]}]}
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


def _normalize_excerpt_text(text: str) -> str:
    lines = []
    for raw_line in (text or "").replace("\x00", " ").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _clip_text(text: str, limit: int = PDF_TEXT_SNIPPET_LIMIT) -> str:
    text = _normalize_excerpt_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n..."


def prepare_pdf_for_ai(input_pdf_path: str) -> str:
    """
    AI에는 PDF 바이너리 대신 텍스트 기반 분석만 전달한다.
    별도로 최적화한 복사본 PDF를 만들어 보관하지만 실제 텍스트 추출은 원본 PDF에서 수행한다.
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
            containment = 0.0
            if qc and rc and min(len(qc), len(rc)) >= 6 and (qc in rc or rc in qc):
                shorter = min(len(qc), len(rc))
                longer = max(len(qc), len(rc))
                if shorter / max(longer, 1) >= 0.72:
                    containment = 95.0
            score = float(max(
                fuzz.WRatio(qc, rc),
                fuzz.token_sort_ratio(qc, rc),
                containment,
            ))
            best = max(best, score)
    return best


def _cluster_positions(values: List[float], threshold: float) -> List[List[float]]:
    if not values:
        return []
    values = sorted(values)
    clusters: List[List[float]] = [[values[0]]]
    for value in values[1:]:
        center = sum(clusters[-1]) / len(clusters[-1])
        if abs(value - center) <= threshold:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def _build_column_bounds(xs: List[float], page_width: float) -> List[Tuple[float, float]]:
    if not xs:
        return [(0.0, page_width)]

    threshold = max(36.0, min(page_width * 0.12, 96.0))
    clusters = _cluster_positions(xs, threshold)
    centers = [sum(cluster) / len(cluster) for cluster in clusters]
    if len(centers) == 1:
        return [(0.0, page_width)]

    mids = [(centers[i] + centers[i + 1]) / 2.0 for i in range(len(centers) - 1)]
    bounds: List[Tuple[float, float]] = []
    for idx in range(len(centers)):
        left = 0.0 if idx == 0 else max(0.0, mids[idx - 1] - 8.0)
        right = page_width if idx == len(centers) - 1 else min(page_width, mids[idx] + 8.0)
        bounds.append((left, right))
    return bounds


def _find_column(value: float, bounds: List[Tuple[float, float]]) -> int:
    for idx, (left, right) in enumerate(bounds):
        if left <= value < right or (idx == len(bounds) - 1 and value <= right):
            return idx
    if not bounds:
        return 0
    distances = [abs(((left + right) / 2.0) - value) for left, right in bounds]
    return distances.index(min(distances))


def _word_xmid(word) -> float:
    return (float(word[0]) + float(word[2])) / 2.0


def _word_ymid(word) -> float:
    return (float(word[1]) + float(word[3])) / 2.0


def _group_words(words: List[tuple], y_threshold: float = 4.5) -> List[List[tuple]]:
    groups: List[List[tuple]] = []
    for word in sorted(words, key=lambda w: (_word_ymid(w), float(w[0]))):
        y_mid = _word_ymid(word)
        if not groups:
            groups.append([word])
            continue
        last_group = groups[-1]
        last_y = sum(_word_ymid(item) for item in last_group) / len(last_group)
        if abs(y_mid - last_y) <= y_threshold:
            last_group.append(word)
        else:
            groups.append([word])
    for group in groups:
        group.sort(key=lambda w: float(w[0]))
    return groups


def _build_region_text_from_words(words: List[tuple]) -> str:
    lines = []
    for group in _group_words(words):
        parts = [str(word[4]).strip() for word in group if str(word[4]).strip()]
        if parts:
            lines.append(" ".join(parts))
    return _normalize_excerpt_text("\n".join(lines))


def _has_nearby_danga(words: List[tuple], label_word: tuple) -> bool:
    label_x = _word_xmid(label_word)
    label_y = _word_ymid(label_word)
    for word in words:
        if str(word[4]).strip() != "단가":
            continue
        if abs(_word_xmid(word) - label_x) <= 42 and -4 <= (_word_ymid(word) - label_y) <= 14:
            return True
    return False


def _price_candidate_value(word: tuple) -> Optional[int]:
    raw = str(word[4]).strip()
    if not (re.fullmatch(r"\d{2,6}", raw) or re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw)):
        return None
    value = _safe_int(raw)
    if value is None or value <= 0:
        return None
    return value


def _extract_prices_from_words(words: List[tuple]) -> PriceSet:
    labels: List[Tuple[str, float, float]] = []
    for word in words:
        text = str(word[4]).strip()
        if text == "규격" and _has_nearby_danga(words, word):
            labels.append(("spec_price", _word_xmid(word), _word_ymid(word)))
        elif re.fullmatch(r"(?i)(kg|l)", text) and _has_nearby_danga(words, word):
            labels.append(("kg_price", _word_xmid(word), _word_ymid(word)))
        elif text in {"개당", "장당", "조각당", "팩당"} and _has_nearby_danga(words, word):
            labels.append(("unit_price", _word_xmid(word), _word_ymid(word)))

    if not labels:
        return PriceSet()

    labels.sort(key=lambda item: item[1])
    label_y = sum(item[2] for item in labels) / len(labels)

    price_words: List[Tuple[tuple, int]] = []
    for word in words:
        value = _price_candidate_value(word)
        if value is not None:
            price_words.append((word, value))

    price_groups: List[List[Tuple[tuple, int]]] = []
    for entry in sorted(price_words, key=lambda item: (_word_ymid(item[0]), _word_xmid(item[0]))):
        if not price_groups:
            price_groups.append([entry])
            continue
        last_group = price_groups[-1]
        last_y = sum(_word_ymid(item[0]) for item in last_group) / len(last_group)
        if abs(_word_ymid(entry[0]) - last_y) <= 5.0:
            last_group.append(entry)
        else:
            price_groups.append([entry])

    best_group: List[Tuple[tuple, int]] = []
    best_score: Optional[float] = None
    best_group_y = -1.0
    for group in price_groups:
        group_y = sum(_word_ymid(item[0]) for item in group) / len(group)
        dy = group_y - label_y
        if dy < -22 or dy > 12:
            continue
        score = abs(dy) * 10.0 + abs(len(group) - len(labels)) * 20.0
        if best_score is None or score < best_score or (score == best_score and group_y > best_group_y):
            best_score = score
            best_group = group
            best_group_y = group_y

    if not best_group:
        return PriceSet()

    ordered_prices = [value for _, value in sorted(best_group, key=lambda item: _word_xmid(item[0]))]
    matched: Dict[str, Optional[int]] = {
        "spec_price": None,
        "kg_price": None,
        "unit_price": None,
    }
    for idx, (kind, _, _) in enumerate(labels):
        if idx < len(ordered_prices):
            matched[kind] = ordered_prices[idx]

    return PriceSet(
        spec_price=matched["spec_price"],
        kg_price=matched["kg_price"],
        unit_price=matched["unit_price"],
    )


def _is_generic_title_line(line: str) -> bool:
    compact = re.sub(r"[\s()]+", "", line)
    compact = compact.replace("／", "/")
    if compact in {"통", "슬라이스", "판", "채", "볼", "사각", "해삼", "종합", "슬라이스/채"}:
        return True
    if line in {"개봉후 냉장", "개봉 후 냉장", "개봉 후 냉장보관"}:
        return True
    if line.startswith(("(", ")")):
        return True
    if re.match(r"^(?:약\s*)?\d", line):
        return True
    if "│" in line or "|" in line:
        return True
    if line.startswith(("㋘", "•", "-", "*")):
        return True
    if re.search(r"(네니아레시피|레시피|재료|1인분|초등|기준|농도조절|포스터|이벤트|문의|홍보|tip|recipe)", line, flags=re.I):
        return True
    if re.search(r"\bD-\d+\b", line, flags=re.I):
        return True
    if re.search(r"(입학\s*100일|오늘을\s*축하|설렘과\s*용기|저탄소식단|한\s*끼의\s*선택|빵순이|빵돌이)", line):
        return True
    return False


def _best_title_line(lines: List[str], catalog_rows: List[dict]) -> Tuple[str, Optional[dict], float]:
    best_line = ""
    best_row = None
    best_score = 0.0
    skip_tokens = [
        "보관", "알러지", "원재료", "조리", "규격단가", "개당", "콤비", "모드",
        "kg단가", "KG단가", "l단가", "L단가", "규격", "단가", "원산지", "함량", "소비기한",
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _is_generic_title_line(line):
            continue
        if len(line) < 3 or len(line) > 40:
            continue
        if line.endswith((".", "!", "?")):
            continue
        if line in {"냉동", "냉장", "상온", "개봉후 냉장", "개봉 후 냉장", "개봉 후 냉장보관"}:
            continue
        if "%" in line:
            continue
        if "/" in line and "(" not in line:
            continue
        if line.count(",") >= 1:
            continue
        if any(token.lower() in line.lower() for token in skip_tokens):
            continue
        if re.search(r"\d{2,}", line) and "(" not in line:
            continue

        for row in catalog_rows:
            score = _name_similarity(line, row)
            if re.match(r"^(국산|국내산|유기농|무농약|무항생제|호주산|미국산|브라질산|신안산)\b", line) and score < 95:
                continue
            if line.startswith("네니아 ") and score < 98:
                continue
            if re.search(r"[로을를은는이가에의]$", line) and score < 98:
                continue
            if score > best_score:
                best_line = line
                best_row = row
                best_score = score
    return best_line, best_row, best_score


def _extract_discount(text: str) -> Tuple[bool, Optional[int], Optional[str]]:
    if not re.search(r"(sale|할인|행사)", text, flags=re.I):
        return False, None, None
    rate_match = re.search(r"(\d{1,2})\s*%", text)
    rate = int(rate_match.group(1)) if rate_match else None
    label = rate_match.group(0) if rate_match else "할인"
    return True, rate, label


def _extract_exclusion(text: str) -> Tuple[bool, Optional[str]]:
    for keyword in ["증정", "사은품", "세트상품", "세트", "무료"]:
        if keyword in text:
            return True, f"{keyword} 문구가 있어 검수 제외로 처리합니다."
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
    kg_match = re.search(r"(?:kg|l)\s*단가\s*[:：]?\s*([\d,]+)", text, flags=re.I)
    unit_match = re.search(r"(?:개당|장당|조각당|팩당)\s*단가\s*[:：]?\s*([\d,]+)", text, flags=re.I)
    return PriceSet(
        spec_price=_safe_int(spec_match.group(1)) if spec_match else None,
        kg_price=_safe_int(kg_match.group(1)) if kg_match else None,
        unit_price=_safe_int(unit_match.group(1)) if unit_match else None,
    )


def _collect_targets(pdf_path: str, catalog_rows: List[dict]) -> List[dict]:
    doc = fitz.open(pdf_path)
    targets: List[dict] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        raw_blocks = [b for b in page.get_text("blocks") if str(b[4]).strip()]
        page_words = page.get_text("words")

        candidates: List[dict] = []
        for block in raw_blocks:
            x0, y0, x1, y1, text, *_ = block
            lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
            if not lines:
                continue
            title_line, matched_row, score = _best_title_line(lines, catalog_rows)
            if not matched_row or score < 90:
                continue
            candidates.append({
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "line": title_line,
                "score": score,
                "matched_row": matched_row,
            })

        column_bounds = _build_column_bounds([((anchor["x0"] + anchor["x1"]) / 2.0) for anchor in candidates], page_width)
        anchors: List[dict] = []
        for candidate in sorted(candidates, key=lambda x: (x["y0"], x["x0"], -x["score"])):
            anchor = dict(candidate)
            anchor["column"] = _find_column((anchor["x0"] + anchor["x1"]) / 2.0, column_bounds)

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
            region_words = []
            for word in page_words:
                x_mid = _word_xmid(word)
                y_mid = _word_ymid(word)
                column = _find_column(x_mid, column_bounds)
                if column != anchor["column"]:
                    continue
                if y_mid < anchor["y0"] - 2 or y_mid >= next_y - 2:
                    continue
                region_words.append(word)
            region_text = _build_region_text_from_words(region_words)
            if not region_text:
                continue

            explicit_discount, discount_rate, discount_label = _extract_discount(region_text)
            excluded, exclusion_reason = _extract_exclusion(region_text)

            targets.append({
                "id": f"p{page_index + 1}_{len(targets) + 1}",
                "page": page_index + 1,
                "product_name": anchor["line"],
                "anchor_title": anchor["line"],
                "score": anchor["score"],
                "excerpt_text": _clip_text(region_text),
                "explicit_discount": explicit_discount,
                "discount_rate": discount_rate,
                "discount_label": discount_label,
                "excluded": excluded,
                "exclusion_reason": exclusion_reason,
                "rule_spec": _rule_based_spec(region_text),
                "rule_prices": _extract_prices_from_words(region_words),
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


def _has_extracted_signal(
    spec_text: str,
    ingredient_content: Optional[str],
    spec_price: Optional[int],
    kg_price: Optional[int],
    unit_price: Optional[int],
) -> bool:
    return any([
        bool(spec_text),
        bool(ingredient_content),
        spec_price is not None,
        kg_price is not None,
        unit_price is not None,
    ])


def _make_item(
    target: dict,
    optimized_pdf_path: str,
    ai: dict,
    items: List[ParsedItem],
    product_name: str,
    spec_text: str,
    ingredient_content: Optional[str],
    spec_price: Optional[int],
    kg_price: Optional[int],
    unit_price: Optional[int],
    evidence_suffix: Optional[str] = None,
    variant_raw: Optional[dict] = None,
) -> None:
    evidence_parts = [
        f"anchor={target['anchor_title']}",
        f"excerpt={target['excerpt_text'][:240]}",
    ]
    if ai.get("evidence"):
        evidence_parts.append(f"ai={_clean_text(str(ai.get('evidence')))}")
    if evidence_suffix:
        evidence_parts.append(f"variant={_clean_text(evidence_suffix)}")

    items.append(
        ParsedItem(
            page=target["page"],
            item_index=len(items) + 1,
            product_name=product_name,
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
                "anchor_title": target["anchor_title"],
                "excerpt_text": target["excerpt_text"],
                "optimized_pdf": optimized_pdf_path,
                "ai": ai,
                "variant": variant_raw,
            },
        )
    )


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

        base_ingredient = _clean_text(ai.get("ingredient_content") or "") or None
        variants = ai.get("variants") if isinstance(ai.get("variants"), list) else []
        emitted_variant = False

        for variant in variants:
            if not isinstance(variant, dict):
                continue

            product_name = _clean_text(variant.get("product_name") or target["product_name"]) or target["product_name"]
            spec_text = _clean_text(variant.get("spec_text") or "")
            ingredient_content = _clean_text(variant.get("ingredient_content") or base_ingredient or "") or None
            spec_price = _safe_int(variant.get("spec_price"))
            kg_price = _safe_int(variant.get("kg_price"))
            unit_price = _safe_int(variant.get("unit_price"))

            if not _has_extracted_signal(spec_text, ingredient_content, spec_price, kg_price, unit_price):
                continue

            _make_item(
                target=target,
                optimized_pdf_path=optimized_pdf_path,
                ai=ai,
                items=items,
                product_name=product_name,
                spec_text=spec_text,
                ingredient_content=ingredient_content,
                spec_price=spec_price,
                kg_price=kg_price,
                unit_price=unit_price,
                evidence_suffix=str(variant.get("evidence") or ""),
                variant_raw=variant,
            )
            emitted_variant = True

        if emitted_variant:
            continue

        spec_text = _clean_text(ai.get("spec_text") or "")
        ingredient_content = base_ingredient
        spec_price = _safe_int(ai.get("spec_price"))
        kg_price = _safe_int(ai.get("kg_price"))
        unit_price = _safe_int(ai.get("unit_price"))

        ai_has_signal = _has_extracted_signal(spec_text, ingredient_content, spec_price, kg_price, unit_price)
        if ai_has_signal:
            if not spec_text:
                spec_text = _clean_text(target["rule_spec"] or "")
            if rule_prices.spec_price is not None:
                spec_price = rule_prices.spec_price
            if rule_prices.kg_price is not None:
                kg_price = rule_prices.kg_price
            if rule_prices.unit_price is not None:
                unit_price = rule_prices.unit_price
        elif not ai:
            spec_text = _clean_text(target["rule_spec"] or "")
            spec_price = rule_prices.spec_price
            kg_price = rule_prices.kg_price
            unit_price = rule_prices.unit_price
        else:
            continue

        if not _has_extracted_signal(spec_text, ingredient_content, spec_price, kg_price, unit_price):
            continue

        _make_item(
            target=target,
            optimized_pdf_path=optimized_pdf_path,
            ai=ai,
            items=items,
            product_name=target["product_name"],
            spec_text=spec_text,
            ingredient_content=ingredient_content,
            spec_price=spec_price,
            kg_price=kg_price,
            unit_price=unit_price,
        )

    return items
