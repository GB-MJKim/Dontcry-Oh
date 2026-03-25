import os
import re
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import openpyxl
from rapidfuzz import fuzz

from .settings import MASTER_XLSX_PATH

EXCEL_LAYOUT = {
    "start_row": 4,
    "code": 1,
    "name": 3,
    "spec": 4,
    "regions": {
        "수도권": {"spec": 17, "kg": 18, "unit": 19},
        "경상권": {"spec": 20, "kg": None, "unit": 21},
        "경남부산": {"spec": 22, "kg": None, "unit": 23},
        "호남권": {"spec": 24, "kg": None, "unit": 25},
    },
}
NAME_MATCH_THRESHOLD = 58.0
REMOVE_BRACKET_WORDS = [
    "냉동", "냉장", "상온", "생지", "반제", "완제",
    "계절상품", "26년신상품", "26년 신상품", "신상품",
]
EXCLUDE_KEYWORDS = ["증정", "사은품", "세트상품", "세트", "덤"]

CATALOG_CACHE: Optional[List[dict]] = None


def save_master_excel(src_path: str) -> None:
    os.makedirs(os.path.dirname(str(MASTER_XLSX_PATH)), exist_ok=True)
    shutil.copyfile(src_path, MASTER_XLSX_PATH)
    clear_cache()


def clear_cache() -> None:
    global CATALOG_CACHE
    CATALOG_CACHE = None


def _safe_str(v) -> str:
    return "" if v is None else str(v).strip()


def _to_number(v) -> Optional[int]:
    if v in (None, "", "-", "None"):
        return None
    if isinstance(v, (int, float)):
        return int(round(float(v)))
    s = str(v).strip().replace(",", "").replace("원", "").replace(" ", "")
    if not s or s.lower() == "nan" or s == "-":
        return None
    try:
        return int(round(float(s)))
    except Exception:
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else None


def _normalize_text(text: str) -> str:
    s = _safe_str(text).replace("\n", " ").replace("\r", " ")
    s = s.replace("×", "x").replace("＊", "*")
    s = s.replace("–", "-").replace("—", "-").replace("~", "-")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _clean_brackets_for_name(text: str) -> str:
    s = _safe_str(text)

    def repl(match: re.Match) -> str:
        inner = _normalize_text(match.group(1)).replace(" ", "")
        for word in REMOVE_BRACKET_WORDS:
            if word.replace(" ", "") in inner:
                return " "
        return f" {match.group(1)} "

    return re.sub(r"\((.*?)\)", repl, s)


def _normalize_name(text: str) -> str:
    s = _clean_brackets_for_name(text)
    s = _normalize_text(s)
    s = s.replace("/", " ").replace("·", " ").replace("_", " ")
    s = s.replace("-", " ").replace(":", " ").replace(",", " ").replace("*", " ")
    s = re.sub(r"부재료.*", " ", s)
    s = re.sub(r"꼼꼼히.*", " ", s)
    s = re.sub(r"확인합니다.*", " ", s)
    s = re.sub(r"\d+\s*%", " ", s)
    s = re.sub(r"[^0-9a-z가-힣 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")


def _normalize_spec(text: str) -> str:
    s = _normalize_text(text)
    s = re.sub(r"\s+", "", s)
    s = s.replace("g×", "gx").replace("kg×", "kgx")
    s = s.replace("개입", "개").replace("내외", "").replace("약", "")
    return s


def _extract_numeric_tokens(text: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?(?:kg|g|ml|l|개|장|입|x)?", _normalize_spec(text))


def _spec_similarity(a: str, b: str) -> float:
    a_n = _normalize_spec(a)
    b_n = _normalize_spec(b)
    if not a_n or not b_n:
        return 0.0
    base = max(
        fuzz.WRatio(a_n, b_n),
        fuzz.partial_ratio(a_n, b_n),
        fuzz.token_sort_ratio(a_n, b_n),
    )
    a_nums = set(_extract_numeric_tokens(a_n))
    b_nums = set(_extract_numeric_tokens(b_n))
    if a_nums and b_nums:
        inter = len(a_nums & b_nums)
        union = len(a_nums | b_nums)
        if union:
            base = max(base, 100.0 * inter / union)
    return float(base)


def _expand_variants_from_bracket(base: str, inner: str) -> List[str]:
    out: List[str] = [f"{base} {inner}"]
    if ":" in inner:
        left, right = inner.split(":", 1)
        left = left.strip()
        right_parts = [x.strip() for x in right.split("/") if x.strip()]
        if left:
            out.append(f"{base} {left}")
        for part in right_parts:
            out.append(f"{base} {part}")
            if left:
                out.append(f"{base} {left} {part}")
    else:
        for part in [x.strip() for x in inner.split("/") if x.strip()]:
            out.append(f"{base} {part}")
    return out


def name_candidates(text: str) -> List[str]:
    raw = _safe_str(text).replace("\n", " ").strip()
    if not raw:
        return []
    cands: List[str] = [raw]
    match = re.search(r"^(.*?)\((.*?)\)\s*$", raw)
    if match:
        base = match.group(1).strip()
        inner = match.group(2).strip()
        cands.append(base)
        cands.extend(_expand_variants_from_bracket(base, inner))
    cands.append(re.sub(r"\(.*?\)", " ", raw))
    cands.extend([x.strip() for x in re.split(r"[\/]", raw) if x.strip()])

    normalized: List[str] = []
    seen = set()
    for cand in cands:
        norm = _normalize_name(cand)
        if norm and norm not in seen:
            seen.add(norm)
            normalized.append(norm)
    return normalized


def normalize_name(text: str) -> str:
    return _normalize_name(text)


def normalize_spec(text: str) -> str:
    return _normalize_spec(text)


def _containment_bonus(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    shorter = min(len(a), len(b))
    if shorter >= 6 and (a in b or b in a):
        return 95.0
    return 0.0


def load_master_df() -> List[dict]:
    global CATALOG_CACHE
    if CATALOG_CACHE is not None:
        return CATALOG_CACHE
    if not os.path.exists(MASTER_XLSX_PATH):
        CATALOG_CACHE = []
        return CATALOG_CACHE

    wb = openpyxl.load_workbook(MASTER_XLSX_PATH, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows: List[dict] = []
    for row_idx in range(EXCEL_LAYOUT["start_row"], ws.max_row + 1):
        name = _safe_str(ws.cell(row_idx, EXCEL_LAYOUT["name"]).value)
        spec = _safe_str(ws.cell(row_idx, EXCEL_LAYOUT["spec"]).value)
        if not name or not spec:
            continue
        row = {
            "row_index": row_idx,
            "code": _safe_str(ws.cell(row_idx, EXCEL_LAYOUT["code"]).value),
            "name": name,
            "spec": spec,
            "normalized_name": _normalize_name(name),
            "normalized_spec": _normalize_spec(spec),
            "name_candidates": name_candidates(name),
        }
        for region, cols in EXCEL_LAYOUT["regions"].items():
            row[f"{region}_spec_price"] = _to_number(ws.cell(row_idx, cols["spec"]).value) if cols["spec"] else None
            row[f"{region}_kg_price"] = _to_number(ws.cell(row_idx, cols["kg"]).value) if cols["kg"] else None
            row[f"{region}_unit_price"] = _to_number(ws.cell(row_idx, cols["unit"]).value) if cols["unit"] else None
        rows.append(row)
    CATALOG_CACHE = rows
    return CATALOG_CACHE


def summarize_master_df(df: List[dict]) -> Dict:
    if not df:
        return {"columns": [], "rows": [], "count": 0}
    preview_cols = [
        "code", "name", "spec",
        "수도권_spec_price", "수도권_kg_price", "수도권_unit_price",
        "경상권_spec_price", "경상권_unit_price",
        "경남부산_spec_price", "경남부산_unit_price",
        "호남권_spec_price", "호남권_unit_price",
    ]
    rows = [{k: row.get(k) for k in preview_cols} for row in df[:200]]
    return {"columns": preview_cols, "rows": rows, "count": len(df)}


def get_field_map(df: List[dict]) -> Dict[str, Optional[str]]:
    if not df:
        return {}
    return {
        "name": "name",
        "spec": "spec",
        "수도권_spec": "수도권_spec_price",
        "수도권_kg": "수도권_kg_price",
        "수도권_unit": "수도권_unit_price",
        "경상권_spec": "경상권_spec_price",
        "경상권_unit": "경상권_unit_price",
        "경남부산_spec": "경남부산_spec_price",
        "경남부산_unit": "경남부산_unit_price",
        "호남권_spec": "호남권_spec_price",
        "호남권_unit": "호남권_unit_price",
    }


def clean_product_name(name: str) -> str:
    return _safe_str(name).replace("\n", " ").strip()


def find_best_match(
    df: List[dict],
    query: str,
    spec: str = "",
    prices: Optional[Dict[str, Optional[int]]] = None,
    region: str = "수도권",
) -> Tuple[Optional[dict], float]:
    if not df:
        return None, 0.0

    query_candidates = name_candidates(query)
    if not query_candidates:
        return None, 0.0

    best_row: Optional[dict] = None
    best_score = 0.0

    for row in df:
        row_pool = list(row.get("name_candidates") or [])
        row_pool.append(row.get("normalized_name", ""))
        name_score = 0.0
        for qc in query_candidates:
            for rc in row_pool:
                if not qc or not rc:
                    continue
                base_score = float(max(
                        fuzz.WRatio(qc, rc),
                        fuzz.token_sort_ratio(qc, rc),
                        _containment_bonus(qc, rc),
                    ))
                if base_score >= 95:
                    base_score += min(len(qc), len(rc)) * 0.2
                name_score = max(name_score, base_score)

        spec_score = _spec_similarity(spec, row.get("spec", "")) if spec else 0.0
        price_bonus = 0.0
        if prices:
            for kind, weight in (("spec_price", 8.0), ("kg_price", 6.0), ("unit_price", 6.0)):
                left = prices.get(kind)
                right = row.get(f"{region}_{kind}")
                if left is not None and right is not None:
                    if abs(int(left) - int(right)) <= 1:
                        price_bonus += weight
        total = round(name_score * 0.78 + spec_score * 0.22 + price_bonus, 2)
        if total > best_score:
            best_score = total
            best_row = row

    if not best_row or best_score < NAME_MATCH_THRESHOLD:
        return None, best_score
    return best_row, best_score


def extract_master_prices(row: dict, region: str, df: List[dict]) -> Dict[str, Optional[int]]:
    return {
        "spec_price": row.get(f"{region}_spec_price"),
        "kg_price": row.get(f"{region}_kg_price"),
        "unit_price": row.get(f"{region}_unit_price"),
        "spec_text": row.get("spec"),
        "product_name": row.get("name"),
    }
