from typing import List, Dict, Optional
from .models import ParsedItem, InspectionRow
from . import data_manager

def _apply_discount(price: Optional[int], rate: Optional[int]) -> Optional[int]:
    if price is None:
        return None
    if rate is None:
        return price
    return int(round(price * (100 - rate) / 100.0))

def _same_number(a: Optional[int], b: Optional[int], tolerance: int = 0) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance

def compare_items(items: List[ParsedItem], master_df, region: str) -> List[InspectionRow]:
    rows: List[InspectionRow] = []
    for idx, item in enumerate(items, start=1):
        notes = []
        if item.excluded:
            rows.append(InspectionRow(
                index=idx,
                status="제외",
                product_name_pdf=item.product_name,
                product_name_master=None,
                match_score=0.0,
                pdf_spec=item.spec_text,
                master_spec=None,
                pdf_prices={"spec_price": item.prices.spec_price, "kg_price": item.prices.kg_price, "unit_price": item.prices.unit_price},
                master_prices={"spec_price": None, "kg_price": None, "unit_price": None},
                notes=[item.exclusion_reason or "검수 제외 항목입니다."],
            ))
            continue

        row, score = data_manager.find_best_match(master_df, item.product_name)
        if not row:
            rows.append(InspectionRow(
                index=idx,
                status="오류",
                product_name_pdf=item.product_name,
                product_name_master=None,
                match_score=0.0,
                pdf_spec=item.spec_text,
                master_spec=None,
                pdf_prices={"spec_price": item.prices.spec_price, "kg_price": item.prices.kg_price, "unit_price": item.prices.unit_price},
                master_prices={"spec_price": None, "kg_price": None, "unit_price": None},
                notes=["기준 데이터에서 상품을 찾지 못했습니다."],
            ))
            continue

        master = data_manager.extract_master_prices(row, region, master_df)
        expected_spec = master["spec_price"]
        expected_kg = master["kg_price"]
        expected_unit = master["unit_price"]

        status = "확인필요"

        if item.explicit_discount:
            notes.append("할인 문구가 명시되어 할인 상품으로 처리했습니다.")
            if item.discount_rate is not None:
                notes.append(f"할인율 {item.discount_rate}% 기준으로 예상 가격을 계산했습니다.")
                expected_spec = _apply_discount(expected_spec, item.discount_rate)
                expected_kg = _apply_discount(expected_kg, item.discount_rate)
                expected_unit = _apply_discount(expected_unit, item.discount_rate)
                status = "할인적용"
            else:
                notes.append("할인율은 없고 할인 문구만 있어 정보성 안내로 표시합니다.")
                status = "할인적용"

        problems = []
        if item.prices.spec_price is None:
            problems.append("규격단가를 PDF에서 찾지 못했습니다.")
        elif expected_spec is not None and not _same_number(item.prices.spec_price, expected_spec):
            problems.append("규격단가가 기준 데이터와 다릅니다.")
        if region == "수도권":
            if item.prices.kg_price is None:
                problems.append("KG단가를 PDF에서 찾지 못했습니다.")
            elif expected_kg is not None and not _same_number(item.prices.kg_price, expected_kg):
                problems.append("KG단가가 기준 데이터와 다릅니다.")
        if item.prices.unit_price is None and master["unit_price"] is not None:
            problems.append("개당단가를 PDF에서 찾지 못했습니다.")
        elif master["unit_price"] is not None and item.prices.unit_price is not None and not _same_number(item.prices.unit_price, expected_unit):
            problems.append("개당단가가 기준 데이터와 다릅니다.")

        if problems:
            status = "오류"
            notes.extend(problems)
        elif status != "할인적용":
            status = "정상"

        rows.append(InspectionRow(
            index=idx,
            status=status,
            product_name_pdf=item.product_name,
            product_name_master=master["product_name"],
            match_score=score,
            pdf_spec=item.spec_text,
            master_spec=master["spec_text"],
            pdf_prices={"spec_price": item.prices.spec_price, "kg_price": item.prices.kg_price, "unit_price": item.prices.unit_price},
            master_prices={"spec_price": expected_spec, "kg_price": expected_kg, "unit_price": expected_unit},
            notes=notes,
        ))
    return rows
