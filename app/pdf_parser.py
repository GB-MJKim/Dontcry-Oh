import fitz
import re
from typing import List, Dict, Any, Optional
from .models import ParsedItem, PriceSet

EXCLUSION_KEYWORDS = ["증정", "사은품", "무료", "세트상품"]
SPEC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.I)
PRICE_NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{3,6})(?!\d)")
TITLE_NOISE_PATTERNS = [
    r"부재료.*", r"꼼꼼히.*", r"확인합니다.*", r"국산 통팥앙금.*",
    r"국산 무화과잼.*", r"국산 유정란.*", r"6월\s*sale.*", r"\d+\s*%.*",
]

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

def normalize_product_name(name: str) -> str:
    s = clean_text(name)
    s = s.replace("(냉동)", "").replace("(상온)", "").replace("(생지)", "")
    for pat in TITLE_NOISE_PATTERNS:
        s = re.sub(pat, "", s, flags=re.I)
    s = re.sub(r"\bSALE\b", "", s, flags=re.I)
    s = re.sub(r"\d+\s*%", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -/·,")
    return s

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
                    "font": sp.get("font", ""),
                })
    return spans

def _guess_card_tops(spans: List[Dict[str, Any]]) -> List[float]:
    ys = []
    for sp in spans:
        if SPEC_RE.search(sp["text"]):
            y = sp["bbox"][1]
            if y > 320:
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
    used = []
    for y in sorted(card_tops):
        y0 = y - 35
        if used and abs(y0 - used[-1][0]) < 80:
            continue
        used.append((y0, y0 + 135))
    for y0, y1 in used:
        for x0, x1 in xs:
            bounds.append((x0, y0, x1, y1))
    return bounds

def _spans_in_rect(spans, rect):
    x0, y0, x1, y1 = rect
    out = []
    for sp in spans:
        sx0, sy0, sx1, sy1 = sp["bbox"]
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(sp)
    return out

def _looks_like_name_line(text: str) -> bool:
    t = clean_text(text)
    if len(t) < 2:
        return False
    if any(k in t for k in ["규격단가", "KG단가", "개당단가", "기준", "PDF", "보관", "알레르기", "오븐", "에어프라이어"]):
        return False
    if re.search(r"\d", t) and any(u in t.lower() for u in ["kg", "g", "ml", "l", "x"]):
        return False
    return True

def _extract_product_name(card_spans: List[Dict[str, Any]]) -> str:
    if not card_spans:
        return ""
    y_min = min(sp["bbox"][1] for sp in card_spans)
    x_min = min(sp["bbox"][0] for sp in card_spans)
    title_zone = []
    for sp in card_spans:
        x0, y0, x1, y1 = sp["bbox"]
        txt = sp["text"]
        if not _looks_like_name_line(txt):
            continue
        if y0 > y_min + 42:
            continue
        if x0 > x_min + 65:
            continue
        score = sp["size"] * 10
        if "Bold" in sp.get("font", ""):
            score += 10
        if len(txt) > 18:
            score -= 8
        if is_red_like(sp["color"]):
            score -= 5
        title_zone.append((score, y0, x0, txt, sp["size"]))
    if not title_zone:
        fallback = [sp for sp in card_spans if _looks_like_name_line(sp["text"])]
        fallback = sorted(fallback, key=lambda x: (x["bbox"][1], x["bbox"][0]))
        return normalize_product_name(" ".join(sp["text"] for sp in fallback[:2]))
    title_zone = sorted(title_zone, key=lambda x: (-x[0], x[1], x[2]))
    best = title_zone[0]
    lines = [best]
    base_y, base_size = best[1], best[4]
    for cand in sorted(title_zone[1:], key=lambda x: (x[1], x[2])):
        if abs(cand[1] - base_y) <= 18 and abs(cand[4] - base_size) <= 2.5:
            if cand[3] not in [l[3] for l in lines]:
                lines.append(cand)
        elif 0 < cand[1] - base_y <= 20 and abs(cand[2] - best[2]) <= 15 and abs(cand[4] - base_size) <= 2.5:
            if cand[3] not in [l[3] for l in lines]:
                lines.append(cand)
    lines = sorted(lines, key=lambda x: (x[1], x[2]))
    return normalize_product_name(" ".join(l[3] for l in lines[:2]))

def _extract_spec(card_spans):
    candidates = []
    for sp in sorted(card_spans, key=lambda x: x["bbox"][1]):
        if SPEC_RE.search(sp["text"]):
            candidates.append(sp["text"])
    return candidates[0] if candidates else ""

def _extract_prices(card_spans):
    nums = []
    for sp in card_spans:
        t = sp["text"]
        for m in PRICE_NUM_RE.finditer(t):
            value = int(m.group(1).replace(",", ""))
            if 100 <= value <= 999999:
                nums.append((sp["bbox"][1], sp["bbox"][0], value, t, sp["color"]))
    nums = sorted(nums)
    spec_price = kg_price = unit_price = None
    for y, x, value, txt, color in nums:
        if "PDF" in txt and value < 1000 and "," not in txt:
            continue
        if 150 <= x <= 210 and spec_price is None:
            spec_price = value
        elif 195 < x <= 245 and kg_price is None:
            kg_price = value
        elif x > 255 and unit_price is None:
            unit_price = value
    ordered = [v for _, _, v, _, _ in nums if v >= 100]
    if spec_price is None and len(ordered) >= 1:
        spec_price = ordered[0]
    if kg_price is None and len(ordered) >= 2:
        kg_price = ordered[1]
    if unit_price is None and len(ordered) >= 3:
        unit_price = ordered[2]
    return PriceSet(spec_price=spec_price, kg_price=kg_price, unit_price=unit_price)

def _extract_discount(card_spans, page_spans, rect):
    card_text = " ".join(sp["text"] for sp in card_spans)
    x0, y0, x1, y1 = rect
    nearby = []
    for sp in page_spans:
        sx0, sy0, sx1, sy1 = sp["bbox"]
        cx, cy = (sx0 + sx1)/2, (sy0 + sy1)/2
        if x0 - 35 <= cx <= x1 + 35 and y0 - 50 <= cy <= y0 + 12:
            nearby.append(sp["text"])
    nearby_text = " ".join(nearby)
    banner_text = f"{card_text} {nearby_text}".lower()
    explicit = ("sale" in banner_text or "할인" in banner_text or "특가" in banner_text) and "%" in banner_text
    rate = None
    m = re.search(r"(\d{1,2})\s*%", banner_text)
    if m:
        rate = int(m.group(1))
    red = any(is_red_like(sp["color"]) for sp in card_spans)
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
            if len(name) < 2:
                lines = [sp["text"] for sp in sorted(card_spans, key=lambda x: (x["bbox"][1], x["bbox"][0])) if _looks_like_name_line(sp["text"])]
                name = normalize_product_name(" ".join(lines[:2]))
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
