from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class PriceSet:
    spec_price: Optional[int] = None
    kg_price: Optional[int] = None
    unit_price: Optional[int] = None

@dataclass
class ParsedItem:
    page: int
    card_index: int
    pdf_name: str
    product_name: str
    spec_text: str
    body_text: str = ""
    prices: PriceSet = field(default_factory=PriceSet)
    discount_rate: Optional[int] = None
    discount_label: Optional[str] = None
    explicit_discount: bool = False
    red_text_detected: bool = False
    excluded: bool = False
    exclusion_reason: Optional[str] = None
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
