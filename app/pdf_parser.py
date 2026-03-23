import fitz
import re
from typing import List, Dict, Any, Optional
from .models import ParsedItem, PriceSet

EXCLUSION_KEYWORDS = ["증정", "사은품", "무료", "세트상품"]
DISCOUNT_KEYWORDS = ["sale", "할인", "%", "특가"]
PRICE_LABEL_RE = re.compile(r"(규격|kg|개당)\s*단가", re.I)
PRICE_NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{3,6})(?!\d)")
SPEC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.I)

def _color_rgb_tuple(color_int: int):
    r = (color_int >> 16) & 255
    g = (color_int >> 8) & 255
    b = color_int & 255
    return r, g, b

def is_red_like(color_int: int) -> bool:
    if not color_int:
        return False
    r, g, b = _color_rgb_tuple(color_int)
    return r > 140 and r > g + 20 and r > b + 20

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def _collect_spans(page) -> List[Dict[str, Any]]:
    data = page.get_text("dict")
    spans = []
    for b in data.get("blocks", []):
        if "lines" not in b:
            continue
        for ln in b["lines"]:
            for sp in ln["spans"]:
                txt = clean_text(sp.get("text", ""))
                if not txt:
                    continue
                spans.append({
                    "text": txt,
                    "bbox": sp["bbox"],
                    "color": sp.get("color", 0),
                    "size": sp.get("size", 0),
                })
    return spans

def _guess_card_tops(spans: List[Dict[str, Any]]) -> List[float]:
    ys = []
    for sp in spans:
        t = sp["text"]
        if SPEC_RE.search(t):
            y = sp["bbox"][1]
            if y > 320:  # 상품 카드 영역 아래만
                ys.append(y)
    ys = sorted(ys)
    groups = []
    for y in ys:
        if not groups or abs(y - groups[-1]) > 120:
            groups.append(y)
    return groups

def _card_bounds(page_rect, card_tops: List[float]) -> List[tuple]:
    xs = [(145, 306), (420, 580)]
    bounds = []
    # inferred rows from tops
    row_ys = []
    for y in card_tops:
        row_ys.append((y - 35, y + 90))
    row_ys = sorted(row_ys)
    used = []
    for y0, y1 in row_ys:
        if used and abs(y0 - used[-1][0]) < 80:
            continue
        used.append((y0, y0 + 120))
    for y0, y1 in used:
        for x0, x1 in xs:
            bounds.append((x0, y0, x1, y1))
    return bounds

def _spans_in_rect(spans, rect):
    x0, y0, x1, y1 = rect
    out = []
    for sp in spans:
        sx0, sy0, sx1, sy1 = sp["bbox"]
        cx, cy = (sx0 + sx1)/2, (sy0 + sy1)/2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(sp)
    return out

def _extract_product_name(card_spans: List[Dict[str, Any]]) -> str:
    candidates = []
    for sp in card_spans:
        txt = sp["text"]
        if any(k in txt for k in ["보관방법", "알러지", "규격", "단가", "개당", "kg", "오븐", "튀김"]):
            continue
        if re.search(r"\d", txt) and any(u in txt.lower() for u in ["kg", "g", "ml", "l"]):
            continue
        # Prefer medium large colored titles
        score = sp["size"] * 10
        if len(txt) < 3:
            score -= 20
        if len(txt) > 25:
            score -= 10
        if is_red_like(sp["color"]) or sp["color"] == 2301728:
            score += 15
        candidates.append((score, txt, sp["bbox"][1]))
    candidates.sort(key=lambda x: (-x[0], x[2]))
    names = []
    for _, txt, _ in candidates[:3]:
        if txt not in names:
            names.append(txt)
    if not names:
        return ""
    # combine up to 2 lines for titles split over lines
    return clean_text(" ".join(names[:2]))

def _extract_spec(card_spans):
    for sp in sorted(card_spans, key=lambda x: x["bbox"][1]):
        t = sp["text"]
        if SPEC_RE.search(t):
            return t
    return ""

def _extract_prices(card_spans):
    nums = []
    for sp in card_spans:
        for m in PRICE_NUM_RE.finditer(sp["text"]):
            value = int(m.group(1).replace(",", ""))
            if 100 <= value <= 999999:
                nums.append((sp["bbox"][1], sp["bbox"][0], value, sp["text"], sp["color"]))
    nums = sorted(nums)
    # infer by labels or x-position
    spec_price = kg_price = unit_price = None
    for y, x, value, txt, color in nums:
        if 150 <= x <= 210:
            spec_price = value
        elif 195 < x <= 245:
            kg_price = value
        elif x > 255:
            unit_price = value
    # fallback by order from lower card area
    ordered = [v for _, _, v, _, _ in nums]
    if spec_price is None and len(ordered) >= 1:
        spec_price = ordered[0]
    if kg_price is None and len(ordered) >= 2:
        kg_price = ordered[1]
    if unit_price is None and len(ordered) >= 3:
        unit_price = ordered[2]
    return PriceSet(spec_price=spec_price, kg_price=kg_price, unit_price=unit_price)

def _extract_discount(card_spans, page_spans, rect):
    card_text = " ".join(sp["text"] for sp in card_spans)
    explicit = any(k in card_text.lower() for k in DISCOUNT_KEYWORDS if k != "%")
    # Include nearby page level sale banner above card
    x0, y0, x1, y1 = rect
    nearby = []
    for sp in page_spans:
        sx0, sy0, sx1, sy1 = sp["bbox"]
        cx, cy = (sx0 + sx1)/2, (sy0 + sy1)/2
        if x0 - 30 <= cx <= x1 + 30 and y0 - 45 <= cy <= y0 + 10:
            nearby.append(sp["text"])
    nearby_text = " ".join(nearby)
    banner_text = f"{card_text} {nearby_text}".lower()
    explicit = explicit or ("sale" in banner_text and "%" in banner_text) or ("할인" in banner_text and "%" in banner_text)
    rate = None
    m = re.search(r"(\d{1,2})\s*%", banner_text)
    if m:
        rate = int(m.group(1))
    red = any(is_red_like(sp["color"]) for sp in card_spans)
    # red only counts when explicit discount exists
    red_text_detected = red and explicit
    label = nearby_text.strip() if explicit and nearby_text.strip() else None
    return explicit, rate, label, red_text_detected

def _is_excluded(name: str, text: str) -> Optional[str]:
    joined = f"{name} {text}"
    if any(k in joined for k in EXCLUSION_KEYWORDS):
        return "증정/사은품/세트상품으로 검수 제외"
    return None

def parse_pdf(pdf_path: str) -> List[ParsedItem]:
    doc = fitz.open(pdf_path)
    items: List[ParsedItem] = []
    card_index = 0
    for pno, page in enumerate(doc, start=1):
        page_spans = _collect_spans(page)
        card_tops = _guess_card_tops(page_spans)
        if not card_tops:
            continue
        bounds = _card_bounds(page.rect, card_tops)
        for rect in bounds:
            card_spans = _spans_in_rect(page_spans, rect)
            if not card_spans:
                continue
            name = _extract_product_name(card_spans)
            spec_text = _extract_spec(card_spans)
            prices = _extract_prices(card_spans)
            body_text = " ".join(sp["text"] for sp in card_spans)
            if not name and not spec_text:
                continue
            card_index += 1
            explicit, rate, label, red_detected = _extract_discount(card_spans, page_spans, rect)
            exclusion_reason = _is_excluded(name, body_text)
            items.append(ParsedItem(
                page=pno,
                card_index=card_index,
                pdf_name=name,
                product_name=name,
                spec_text=spec_text,
                body_text=body_text,
                prices=prices,
                discount_rate=rate,
                discount_label=label,
                explicit_discount=explicit,
                red_text_detected=red_detected,
                excluded=bool(exclusion_reason),
                exclusion_reason=exclusion_reason,
                raw={"rect": rect},
            ))
    return items
