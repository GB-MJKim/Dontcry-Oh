from __future__ import annotations

import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import settings
from .models import ParsedProduct, PdfCard
from .utils import image_to_data_url, load_json, save_json, sha256_file


PROMPT = """
너는 식품 카탈로그 PDF의 상품 카드에서 상품 정보를 구조화해서 추출하는 파서다.
반드시 JSON 하나만 반환한다.
규칙:
1) 상품명은 대표 품명만 추출한다.
2) 규격은 g, kg, ml, L 등이 포함된 대표 규격 한 줄만 추출한다.
3) 가격은 regular_price, kg_price, unit_price, discount_price에 숫자만 넣는다. 없으면 null.
4) 빨간색 가격 또는 빨간색 할인 문구가 보이면 has_red_price=true.
5) 'SALE', '할인', '특가', '%', '행사' 같은 문구가 있으면 has_discount_text=true.
6) 할인율이 명시되어 있으면 discount_rate_percent에 숫자만 넣는다. 예: '10%' -> 10.
7) 신뢰도는 0~1 사이 숫자.
8) 설명문은 넣지 말고 아래 스키마로만 반환한다.

스키마:
{
  "product_name": "",
  "spec": "",
  "regular_price": null,
  "kg_price": null,
  "unit_price": null,
  "discount_price": null,
  "discount_rate_percent": null,
  "has_red_price": false,
  "has_discount_text": false,
  "discount_notes": "",
  "parse_confidence": 0.0
}
""".strip()


class AIProductParser:
    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key and settings.ai_enabled)
        self.client = None
        if self.enabled:
            from openai import OpenAI
            self.client = OpenAI(api_key=settings.openai_api_key)
        self.cache_path = settings.cache_dir / 'ai_parse_cache.json'
        self.cache = load_json(self.cache_path)

    def _cache_key(self, card: PdfCard) -> str:
        return f"{sha256_file(card.image_path)}::{settings.openai_model}"

    def parse_card(self, card: PdfCard) -> ParsedProduct:
        if not self.enabled:
            return self._fallback_parse(card)
        key = self._cache_key(card)
        if key in self.cache:
            data = self.cache[key]
            return self._to_parsed(card, data, source='ai-cache')
        try:
            data = self._call_model(card)
            self.cache[key] = data
            save_json(self.cache_path, self.cache)
            return self._to_parsed(card, data, source='ai')
        except Exception:
            return self._fallback_parse(card)

    def parse_cards(self, cards: list[PdfCard]) -> list[ParsedProduct]:
        if not cards:
            return []
        results: list[ParsedProduct] = []
        with ThreadPoolExecutor(max_workers=settings.max_parallel_cards) as pool:
            futures = {pool.submit(self.parse_card, card): card for card in cards}
            for fut in as_completed(futures):
                results.append(fut.result())
        results.sort(key=lambda x: (x.page_number, x.index_on_page))
        return results

    def _call_model(self, card: PdfCard) -> dict:
        data_url = image_to_data_url(card.image_path)
        completion = self.client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"원문 텍스트:\n{card.raw_text}\n\n빨간 텍스트 개수 추정: {card.red_text_count}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        content = completion.choices[0].message.content or '{}'
        return json.loads(content)

    def _fallback_parse(self, card: PdfCard) -> ParsedProduct:
        lines = [ln.strip() for ln in card.raw_text.splitlines() if ln.strip()]
        name = ''
        spec = ''
        for line in lines:
            if not name and not re.search(r'(원재료|알러지|보관|단가|kg|개당|조리|sale|할인|행사|특가|\d{1,3},\d{3})', line, re.I):
                if 2 <= len(line) <= 40:
                    name = line
                    continue
            if not spec and re.search(r'\b(?:\d+(?:\.\d+)?)\s*(?:kg|g|ml|l)\b', line, re.I):
                spec = line
                break
        nums = []
        for line in lines:
            for m in re.findall(r'\d{1,3}(?:,\d{3})+', line):
                nums.append(int(m.replace(',', '')))
        nums = sorted(set(nums), reverse=True)
        discount_rate = None
        rate_match = re.search(r'(\d{1,2}(?:\.\d+)?)\s*%', card.raw_text)
        if rate_match:
            discount_rate = float(rate_match.group(1))
        discount_words = bool(re.search(r'(sale|할인|행사|특가|\d+\s*%)', card.raw_text, re.I))
        return ParsedProduct(
            page_number=card.page_number,
            index_on_page=card.index_on_page,
            product_name=name,
            spec=spec,
            regular_price=nums[0] if len(nums) >= 1 else None,
            kg_price=nums[1] if len(nums) >= 2 else None,
            unit_price=nums[2] if len(nums) >= 3 else None,
            discount_price=nums[0] if card.red_text_count > 0 and nums else None,
            discount_rate_percent=discount_rate,
            has_red_price=card.red_text_count > 0,
            has_discount_text=discount_words,
            discount_notes='AI 미사용/실패 시 규칙 기반 추출',
            raw_text=card.raw_text,
            parser_source='rule',
            parse_confidence=0.35,
        )

    def _to_parsed(self, card: PdfCard, data: dict, source: str) -> ParsedProduct:
        def to_int(v):
            if v in (None, '', 'null'):
                return None
            try:
                return int(round(float(str(v).replace(',', '').strip())))
            except Exception:
                return None
        def to_float(v):
            if v in (None, '', 'null'):
                return None
            try:
                return float(v)
            except Exception:
                return None
        return ParsedProduct(
            page_number=card.page_number,
            index_on_page=card.index_on_page,
            product_name=str(data.get('product_name') or '').strip(),
            spec=str(data.get('spec') or '').strip(),
            regular_price=to_int(data.get('regular_price')),
            kg_price=to_int(data.get('kg_price')),
            unit_price=to_int(data.get('unit_price')),
            discount_price=to_int(data.get('discount_price')),
            discount_rate_percent=to_float(data.get('discount_rate_percent')),
            has_red_price=bool(data.get('has_red_price')),
            has_discount_text=bool(data.get('has_discount_text')),
            discount_notes=str(data.get('discount_notes') or '').strip(),
            raw_text=card.raw_text,
            parser_source=source,
            parse_confidence=float(data.get('parse_confidence') or 0.0),
        )
