import os
import shutil
import re
import pandas as pd
from typing import Tuple, List, Dict, Optional
from rapidfuzz import process, fuzz
from .settings import MASTER_XLSX_PATH

REGION_KEYS = {
    "수도권": {"spec": [["수도권", "규격단가"], ["수도권학교가"], ["수도권", "학교가"]], "kg": [["수도권", "kg단가"], ["kg단가"]], "unit": [["수도권", "개당단가"], ["개당단가"]]},
    "경상권": {"spec": [["경상권", "규격단가"], ["경상권학교가"], ["경상권", "학교가"]], "unit": [["경상권", "개당단가"], ["개당단가"]]},
    "경남부산": {"spec": [["경남", "규격단가"], ["경남부산학교가"], ["경남부산", "학교가"], ["부산", "학교가"]], "unit": [["경남", "개당단가"], ["경남부산", "개당"], ["부산", "개당"]]},
    "호남권": {"spec": [["호남", "규격단가"], ["호남권학교가"], ["호남", "학교가"]], "unit": [["호남", "개당단가"], ["개당단가"]]},
}
NAME_ALIASES = ["상품명", "제품명", "품명", "상품", "제품", "상품명칭"]
SPEC_ALIASES = ["규격", "중량", "용량", "내용량", "포장규격"]

def save_master_excel(src_path: str) -> None:
    os.makedirs(os.path.dirname(MASTER_XLSX_PATH), exist_ok=True)
    shutil.copyfile(src_path, MASTER_XLSX_PATH)

def _normalize_col(col: str) -> str:
    return str(col).replace(" ", "").replace("\n", "").replace("/", "").replace("·","").replace("_","").replace("-","").lower()

def _find_col(cols, aliases):
    normalized = {_normalize_col(c): c for c in cols}
    for alias in aliases:
        alias_n = _normalize_col(alias)
        for nc, original in normalized.items():
            if alias_n in nc:
                return original
    return None

def _find_region_col(cols, token_groups: List[List[str]]) -> Optional[str]:
    normalized = {_normalize_col(c): c for c in cols}
    for tokens in token_groups:
        toks = [_normalize_col(x) for x in tokens]
        for nc, original in normalized.items():
            if all(tok in nc for tok in toks):
                return original
    return None

def load_master_df() -> pd.DataFrame:
    if not os.path.exists(MASTER_XLSX_PATH):
        return pd.DataFrame()
    return pd.read_excel(MASTER_XLSX_PATH).fillna("")

def summarize_master_df(df: pd.DataFrame) -> Dict:
    if df.empty:
        return {"columns": [], "rows": [], "count": 0}
    return {"columns": list(df.columns), "rows": df.head(200).to_dict(orient="records"), "count": int(len(df))}

def get_field_map(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    cols = list(df.columns)
    fmap = {"name": _find_col(cols, NAME_ALIASES), "spec": _find_col(cols, SPEC_ALIASES)}
    for region, mapping in REGION_KEYS.items():
        for key, token_groups in mapping.items():
            fmap[f"{region}_{key}"] = _find_region_col(cols, token_groups)
    return fmap

def clean_product_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    s = name.replace("\n", " ").replace("(냉동)", "").replace("(상온)", "").replace("(생지)", "")
    s = re.sub(r"부재료.*", "", s)
    s = re.sub(r"꼼꼼히.*", "", s)
    s = re.sub(r"확인합니다.*", "", s)
    s = re.sub(r"6월\s*sale.*", "", s, flags=re.I)
    s = re.sub(r"\d+\s*%", "", s)
    return re.sub(r"\s+", " ", s).strip(" -/·,")

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
    _, score, idx = result
    return df.iloc[int(idx)].to_dict(), float(score)

def _num(v) -> Optional[int]:
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    digits = ''.join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else None

def extract_master_prices(row: dict, region: str, df: pd.DataFrame) -> Dict[str, Optional[int]]:
    fmap = get_field_map(df)
    out = {"spec_price": None, "kg_price": None, "unit_price": None, "spec_text": None, "product_name": None}
    if fmap.get("name"):
        out["product_name"] = str(row.get(fmap["name"], "")).strip()
    if fmap.get("spec"):
        out["spec_text"] = str(row.get(fmap["spec"], "")).strip()
    if fmap.get(f"{region}_spec"):
        out["spec_price"] = _num(row.get(fmap[f"{region}_spec"]))
    if fmap.get(f"{region}_kg"):
        out["kg_price"] = _num(row.get(fmap[f"{region}_kg"]))
    if fmap.get(f"{region}_unit"):
        out["unit_price"] = _num(row.get(fmap[f"{region}_unit"]))
    return out
