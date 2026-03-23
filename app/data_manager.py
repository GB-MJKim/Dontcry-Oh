import re
import os
import shutil
import pandas as pd
from typing import Tuple, List, Dict, Optional
from rapidfuzz import process, fuzz
from .settings import MASTER_XLSX_PATH

REGION_KEYS = {
    "수도권": {"spec": ["수도권", "규격단가"], "kg": ["수도권", "kg단가"], "unit": ["수도권", "개당단가"]},
    "경상권": {"spec": ["경상권", "규격단가"], "unit": ["경상권", "개당단가"]},
    "경남부산": {"spec": ["경남", "규격단가"], "unit": ["경남", "개당단가"]},
    "호남권": {"spec": ["호남", "규격단가"], "unit": ["호남", "개당단가"]},
}

NAME_ALIASES = ["상품명", "제품명", "품명", "상품", "제품"]
SPEC_ALIASES = ["규격", "중량", "용량"]

def save_master_excel(src_path: str) -> None:
    os.makedirs(os.path.dirname(MASTER_XLSX_PATH), exist_ok=True)
    shutil.copyfile(src_path, MASTER_XLSX_PATH)

def _normalize_col(col: str) -> str:
    return str(col).replace(" ", "").replace("\n", "").replace("/", "").replace("·","").lower()

def _find_col(cols, aliases):
    normalized = {_normalize_col(c): c for c in cols}
    for alias in aliases:
        alias_n = _normalize_col(alias)
        for nc, original in normalized.items():
            if alias_n in nc:
                return original
    return None

def _find_region_col(cols, must_contain: List[str]) -> Optional[str]:
    normalized = {_normalize_col(c): c for c in cols}
    tokens = [_normalize_col(x) for x in must_contain]
    for nc, original in normalized.items():
        if all(tok in nc for tok in tokens):
            return original
    return None

def load_master_df() -> pd.DataFrame:
    if not os.path.exists(MASTER_XLSX_PATH):
        return pd.DataFrame()
    df = pd.read_excel(MASTER_XLSX_PATH)
    df = df.fillna("")
    return df

def summarize_master_df(df: pd.DataFrame) -> Dict:
    if df.empty:
        return {"columns": [], "rows": [], "count": 0}
    rows = df.head(200).to_dict(orient="records")
    return {"columns": list(df.columns), "rows": rows, "count": int(len(df))}

def get_field_map(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    cols = list(df.columns)
    fmap = {
        "name": _find_col(cols, NAME_ALIASES),
        "spec": _find_col(cols, SPEC_ALIASES),
    }
    for region, mapping in REGION_KEYS.items():
        for key, parts in mapping.items():
            fmap[f"{region}_{key}"] = _find_region_col(cols, parts)
    return fmap

def clean_product_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    s = (name.replace("\n", " ").replace("(냉동)", "").replace("(상온)", "")
         .replace("(생지)", "").replace("  ", " ").strip())
    s = re.sub(r"부재료.*", "", s)
    s = re.sub(r"꼼꼼히.*", "", s)
    s = re.sub(r"확인합니다.*", "", s)
    s = re.sub(r"6월\s*sale.*", "", s, flags=re.I)
    s = re.sub(r"\d+\s*%", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -/·,")
    return s

def find_best_match(df: pd.DataFrame, query: str) -> Tuple[Optional[dict], float]:
    if df.empty:
        return None, 0.0
    fmap = get_field_map(df)
    if not fmap["name"]:
        return None, 0.0
    names = [clean_product_name(str(x)) for x in df[fmap["name"]].tolist()]
    result = process.extractOne(clean_product_name(query), names, scorer=fuzz.WRatio)
    if not result:
        return None, 0.0
    matched_name, score, idx = result
    row = df.iloc[int(idx)].to_dict()
    return row, float(score)

def _num(v) -> Optional[int]:
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    digits = ''.join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return int(digits)

def extract_master_prices(row: dict, region: str, df: pd.DataFrame) -> Dict[str, Optional[int]]:
    fmap = get_field_map(df)
    out = {"spec_price": None, "kg_price": None, "unit_price": None, "spec_text": None, "product_name": None}
    name_col = fmap.get("name")
    spec_col = fmap.get("spec")
    if name_col:
        out["product_name"] = str(row.get(name_col, "")).strip()
    if spec_col:
        out["spec_text"] = str(row.get(spec_col, "")).strip()
    spec_key = fmap.get(f"{region}_spec")
    kg_key = fmap.get(f"{region}_kg")
    unit_key = fmap.get(f"{region}_unit")
    if spec_key:
        out["spec_price"] = _num(row.get(spec_key))
    if kg_key:
        out["kg_price"] = _num(row.get(kg_key))
    if unit_key:
        out["unit_price"] = _num(row.get(unit_key))
    return out
