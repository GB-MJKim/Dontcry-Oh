from __future__ import annotations

from .excel_matcher import MasterCatalog, REGION_ALIASES
from .models import ComparisonResult, ParsedProduct
from .utils import apply_discount, normalize_name, round_half_up


STATUS_META = {
    'ok': '정상',
    'discount_applied': '할인적용',
    'warning': '확인필요',
    'error': '오류',
}


def _pick_pdf_value(parsed: ParsedProduct, key: str) -> int | None:
    if parsed.discount_rate_percent is not None:
        if key == 'regular_price' and parsed.discount_price is not None:
            return parsed.discount_price
        # KG/개당은 할인된 금액이 별도 표기되지 않는 경우 regular와 같이 붉은색으로 표기되는 레이아웃이 많아
        # 표기값이 있으면 그대로 사용, 없으면 None으로 둔다.
    return getattr(parsed, key)


def compare_products(parsed_products: list[ParsedProduct], catalog: MasterCatalog, region_input: str) -> list[ComparisonResult]:
    region = REGION_ALIASES.get(region_input, region_input)
    results: list[ComparisonResult] = []
    for parsed in parsed_products:
        match, score = catalog.find_best_match(parsed.product_name, parsed.spec)
        notes: list[str] = []
        checks: list[dict] = []
        status = 'ok'

        if not match:
            results.append(ComparisonResult(
                page_number=parsed.page_number,
                index_on_page=parsed.index_on_page,
                region=region,
                status='error',
                status_label=STATUS_META['error'],
                product_name_pdf=parsed.product_name,
                product_name_master='(매칭 실패)',
                spec_pdf=parsed.spec,
                spec_master='',
                matched_score=score,
                price_checks=[],
                notes=['엑셀 기준상품과 매칭되지 않았습니다.'],
                parser_source=parsed.parser_source,
            ))
            continue

        compare_keys = ['regular_price', 'kg_price', 'unit_price'] if region == '수도권' else ['regular_price', 'unit_price']
        if score < 80:
            notes.append('상품명 유사도가 낮습니다. 결과를 한 번 더 확인하세요.')
            status = 'warning'

        expected_prices = match.prices[region]
        discount_mode = parsed.has_red_price or parsed.has_discount_text
        if discount_mode and parsed.discount_rate_percent is None:
            notes.append('빨간색 가격/문구가 감지되어 할인 적용 상품으로 처리했습니다. 할인율이 없어 정보성 안내만 표시합니다.')
            status = 'discount_applied'
        elif discount_mode and parsed.discount_rate_percent is not None:
            notes.append(f'할인율 {parsed.discount_rate_percent:g}% 기준으로 예상 가격을 계산했습니다.')
            status = 'discount_applied'

        for key in compare_keys:
            base_value = expected_prices.get(key)
            expected_value = apply_discount(base_value, parsed.discount_rate_percent) if parsed.discount_rate_percent is not None else round_half_up(base_value)
            pdf_value = getattr(parsed, key)
            if key == 'regular_price' and parsed.discount_price is not None:
                pdf_value = parsed.discount_price if parsed.discount_rate_percent is not None else parsed.regular_price
            if pdf_value is None and key == 'regular_price' and parsed.discount_price is not None:
                pdf_value = parsed.discount_price
            check_status = 'ok'
            label = {'regular_price': '규격단가', 'kg_price': 'KG단가', 'unit_price': '개당단가'}[key]

            if base_value is None:
                check_status = 'warning'
            elif discount_mode and parsed.discount_rate_percent is None:
                check_status = 'discount_applied'
            elif pdf_value is None:
                check_status = 'warning'
                notes.append(f'{label}를 PDF에서 찾지 못했습니다.')
            elif expected_value != pdf_value:
                check_status = 'error'
                status = 'error'
            checks.append({
                'key': key,
                'label': label,
                'base_value': round_half_up(base_value),
                'expected_value': expected_value,
                'pdf_value': pdf_value,
                'status': check_status,
            })

        if status == 'ok' and any(c['status'] == 'warning' for c in checks):
            status = 'warning'
        results.append(ComparisonResult(
            page_number=parsed.page_number,
            index_on_page=parsed.index_on_page,
            region=region,
            status=status,
            status_label=STATUS_META[status],
            product_name_pdf=parsed.product_name,
            product_name_master=match.name,
            spec_pdf=parsed.spec,
            spec_master=match.spec,
            matched_score=score,
            price_checks=checks,
            notes=notes,
            parser_source=parsed.parser_source,
        ))
    return results
