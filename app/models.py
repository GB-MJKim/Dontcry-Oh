from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MasterRow:
    code: str
    name: str
    spec: str
    normalized_name: str
    normalized_spec: str
    prices: dict[str, dict[str, float | None]]


@dataclass
class PdfCard:
    page_number: int
    index_on_page: int
    bbox: tuple[float, float, float, float]
    raw_text: str
    image_path: str
    red_text_count: int
    extracted_numbers: list[str] = field(default_factory=list)


@dataclass
class ParsedProduct:
    page_number: int
    index_on_page: int
    product_name: str = ''
    spec: str = ''
    regular_price: int | None = None
    kg_price: int | None = None
    unit_price: int | None = None
    discount_price: int | None = None
    discount_rate_percent: float | None = None
    has_red_price: bool = False
    has_discount_text: bool = False
    discount_notes: str = ''
    raw_text: str = ''
    parser_source: str = 'rule'
    parse_confidence: float = 0.0


@dataclass
class ComparisonResult:
    page_number: int
    index_on_page: int
    region: str
    status: str
    status_label: str
    product_name_pdf: str
    product_name_master: str
    spec_pdf: str
    spec_master: str
    matched_score: float
    price_checks: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    parser_source: str = 'rule'
