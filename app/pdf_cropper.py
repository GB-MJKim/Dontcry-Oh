from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import statistics
import re
import fitz

from .models import PdfCard
from .utils import extract_numbers, is_red_like, unique_ordered


@dataclass
class Anchor:
    text: str
    bbox: tuple[float, float, float, float]
    page_number: int


def _cluster_positions(values: list[float], threshold: float) -> list[list[float]]:
    if not values:
        return []
    values = sorted(values)
    clusters = [[values[0]]]
    for v in values[1:]:
        if abs(v - statistics.mean(clusters[-1])) <= threshold:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _build_boundaries(centers: list[float], max_value: float, padding: float = 8.0) -> list[tuple[float, float]]:
    if not centers:
        return [(0, max_value)]
    centers = sorted(centers)
    bounds = []
    prev = 0.0
    mids = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    for i, center in enumerate(centers):
        left = 0.0 if i == 0 else mids[i - 1]
        right = max_value if i == len(centers) - 1 else mids[i]
        bounds.append((max(0.0, left - padding), min(max_value, right + padding)))
        prev = right
    return bounds




def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0, inter_y0 = max(ax0, bx0), max(ay0, by0)
    inter_x1, inter_y1 = min(ax1, bx1), min(ay1, by1)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    inter = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / float(area_a + area_b - inter)




def _is_likely_product_card(card: PdfCard) -> bool:
    text = card.raw_text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    hangul_count = sum(1 for ch in text if "가" <= ch <= "힣")
    has_spec = bool(re.search(r"\b(?:\d+(?:\.\d+)?)\s*(?:kg|g|ml|l)\b", text, re.I))
    has_price = len(re.findall(r"\d{1,3}(?:,\d{3})+", text)) >= 1
    name_like_lines = []
    for line in lines:
        if re.search(r"(보관방법|규격|단가|kg|개당|알러지|원재료|인증|조리|sale|할인|행사|특가)", line, re.I):
            continue
        if re.fullmatch(r"[\d,]+", line):
            continue
        if line in {"냉동", "냉장", "상온", "-"}:
            continue
        if sum(1 for ch in line if "가" <= ch <= "힣") >= 3:
            name_like_lines.append(line)
    return hangul_count >= 12 and len(name_like_lines) >= 2 and (has_spec or has_price)

def _dedupe_cards(cards: list[PdfCard]) -> list[PdfCard]:
    kept: list[PdfCard] = []
    for card in sorted(cards, key=lambda c: (c.page_number, c.bbox[1], c.bbox[0], -len(c.raw_text))):
        if len(card.raw_text.splitlines()) < 8 or not _is_likely_product_card(card):
            continue
        duplicate = False
        for other in kept:
            if card.page_number == other.page_number and _iou(card.bbox, other.bbox) > 0.55:
                duplicate = True
                if len(card.raw_text) > len(other.raw_text):
                    kept.remove(other)
                    duplicate = False
                else:
                    break
        if not duplicate:
            kept.append(card)
    # reindex per page
    page_counts = defaultdict(int)
    for card in sorted(kept, key=lambda c: (c.page_number, c.bbox[1], c.bbox[0])):
        page_counts[card.page_number] += 1
        card.index_on_page = page_counts[card.page_number]
    return sorted(kept, key=lambda c: (c.page_number, c.index_on_page))

def detect_cards(pdf_path: str | Path, output_dir: str | Path) -> list[PdfCard]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cards: list[PdfCard] = []

    doc = fitz.open(str(pdf_path))
    for page_index, page in enumerate(doc):
        page_no = page_index + 1
        text_dict = page.get_text('dict')
        spans = []
        for block in text_dict['blocks']:
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    txt = (span.get('text') or '').strip()
                    if not txt:
                        continue
                    spans.append({
                        'text': txt,
                        'bbox': tuple(span['bbox']),
                        'size': float(span.get('size', 0)),
                        'color': int(span.get('color', 0)),
                    })

        anchors: list[Anchor] = []
        for span in spans:
            txt = span['text']
            size = span['size']
            if size < 13:
                continue
            if re.search(r'(단가|보관방법|알러지|원재료|인증|조리)', txt):
                continue
            if re.fullmatch(r'[\d,]+', txt):
                continue
            x0, y0, x1, y1 = span['bbox']
            anchors.append(Anchor(txt, (x0, y0, x1, y1), page_no))

        if not anchors:
            continue

        x_clusters = _cluster_positions([a.bbox[0] for a in anchors], threshold=60)
        y_clusters = _cluster_positions([a.bbox[1] for a in anchors], threshold=85)
        x_centers = [statistics.mean(c) for c in x_clusters]
        y_centers = [statistics.mean(c) for c in y_clusters]
        col_bounds = _build_boundaries(x_centers, page.rect.width)
        row_bounds = _build_boundaries(y_centers, page.rect.height)

        used = set()
        index_on_page = 0
        for row_idx, (top, bottom) in enumerate(row_bounds):
            for col_idx, (left, right) in enumerate(col_bounds):
                cell_anchors = [a for a in anchors if left <= a.bbox[0] <= right and top <= a.bbox[1] <= bottom]
                if not cell_anchors:
                    continue
                rect = fitz.Rect(left, top, right, bottom)
                # tighten to visible text in that area
                cell_spans = [s for s in spans if fitz.Rect(s['bbox']).intersects(rect)]
                if len(cell_spans) < 8:
                    continue
                x0 = max(0.0, min(s['bbox'][0] for s in cell_spans) - 8)
                y0 = max(0.0, min(s['bbox'][1] for s in cell_spans) - 18)
                x1 = min(page.rect.width, max(s['bbox'][2] for s in cell_spans) + 8)
                y1 = min(page.rect.height, max(s['bbox'][3] for s in cell_spans) + 18)
                # Expand downward to capture price/footer area.
                y1 = min(page.rect.height, y1 + 65)
                bbox = (x0, y0, x1, y1)
                key = tuple(round(v, 0) for v in bbox)
                if key in used:
                    continue
                used.add(key)
                index_on_page += 1

                clip = fitz.Rect(bbox)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
                image_path = output_dir / f'page_{page_no:02d}_card_{index_on_page:02d}.png'
                pix.save(str(image_path))
                raw_text = '\n'.join(unique_ordered([s['text'] for s in cell_spans]))
                red_text_count = sum(1 for s in cell_spans if is_red_like(s['color']))
                cards.append(PdfCard(
                    page_number=page_no,
                    index_on_page=index_on_page,
                    bbox=bbox,
                    raw_text=raw_text,
                    image_path=str(image_path),
                    red_text_count=red_text_count,
                    extracted_numbers=[str(n) for n in extract_numbers(raw_text)],
                ))
    return _dedupe_cards(cards)
