from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Iterable

NUM_RE = re.compile(r'(?<!\d)(\d{1,3}(?:,\d{3})+|\d+)(?!\d)')


def normalize_text(text: str) -> str:
    if not text:
        return ''
    cleaned = text.replace('\n', ' ').replace('\r', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()
    cleaned = cleaned.replace('×', 'x').replace('*', '').replace('~', '-')
    cleaned = re.sub(r'\(냉동\)|\(냉장\)|\(상온\)', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def normalize_name(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r'\b(?:sale|행사|특가|할인)\b', '', text, flags=re.I)
    return re.sub(r'\s+', '', text)


def normalize_spec(text: str) -> str:
    text = normalize_text(text)
    text = text.replace(' ', '')
    text = text.replace('g×', 'gx').replace('kg×', 'kgx').replace('ml', 'ml')
    return text


def extract_numbers(text: str) -> list[int]:
    vals = []
    for m in NUM_RE.findall(text or ''):
        try:
            vals.append(int(m.replace(',', '')))
        except ValueError:
            continue
    return vals


def rgb_from_int(color: int) -> tuple[int, int, int]:
    # PyMuPDF stores colors as 0xRRGGBB for simple text spans.
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b = color & 255
    return r, g, b


def is_red_like(color: int) -> bool:
    r, g, b = rgb_from_int(color)
    return r >= 150 and r > g + 25 and r > b + 25


def round_half_up(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(math.floor(float(value) + 0.5))


def apply_discount(value: int | float | None, rate_percent: float | None) -> int | None:
    if value is None or rate_percent is None:
        return None
    return round_half_up(float(value) * (100.0 - rate_percent) / 100.0)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def image_to_data_url(path: str | Path) -> str:
    suffix = Path(path).suffix.lower().lstrip('.') or 'png'
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    return f'data:image/{suffix};base64,{b64}'


def load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding='utf-8'))


def save_json(path: str | Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def unique_ordered(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
