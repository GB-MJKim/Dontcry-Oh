from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PriceSet:
    spec_price: Optional[int] = None
    kg_price: Optional[int] = None
    unit_price: Optional[int] = None


@dataclass
class ParsedItem:
    page: int
    item_index: int
    product_name: str
    spec_text: str
    prices: PriceSet = field(default_factory=PriceSet)
    explicit_discount: bool = False
    discount_rate: Optional[int] = None
    discount_label: Optional[str] = None
    excluded: bool = False
    exclusion_reason: Optional[str] = None
    ingredient_content: Optional[str] = None
    evidence_text: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InspectionRow:
    index: int
    status: str
    product_name_pdf: str
    product_name_master: Optional[str]
    match_score: float
    pdf_spec: str
    master_spec: Optional[str]
    pdf_prices: Dict[str, Optional[int]]
    master_prices: Dict[str, Optional[int]]
    notes: List[str]
