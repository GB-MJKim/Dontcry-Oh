from pathlib import Path
from typing import Iterable, Sequence

from uuid import uuid4

import fitz

from .settings import TEMP_DIR


def _normalize_pages(pages: Sequence[int] | None, page_count: int) -> list[int]:
    if not pages:
        return list(range(1, page_count + 1))

    normalized: list[int] = []
    seen = set()
    for value in pages:
        try:
            page_no = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= page_no <= page_count and page_no not in seen:
            normalized.append(page_no)
            seen.add(page_no)
    return normalized


def _strip_page_images(page: fitz.Page) -> None:
    image_xrefs = sorted({int(image[0]) for image in page.get_images(full=True) if image and int(image[0]) > 0})
    for xref in image_xrefs:
        try:
            page.delete_image(xref)
        except Exception:
            continue
    try:
        page.clean_contents()
    except Exception:
        pass


def build_ai_ready_pdf(
    input_pdf_path: str,
    *,
    pages: Sequence[int] | None = None,
    strip_images: bool = False,
    suffix: str = "optimized",
) -> str:
    src = fitz.open(input_pdf_path)
    output = fitz.open()
    source_name = Path(input_pdf_path).stem.strip() or "document"
    safe_suffix = "".join(ch for ch in suffix if ch.isalnum() or ch in {"_", "-"}) or "optimized"
    target_path = Path(TEMP_DIR) / f"{source_name}_{safe_suffix}_{uuid4().hex[:8]}.pdf"

    try:
        selected_pages = _normalize_pages(pages, len(src))
        if not selected_pages:
            raise RuntimeError("AI용 PDF를 만들 페이지를 찾지 못했습니다.")

        for page_no in selected_pages:
            output.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1)

        if strip_images:
            for page in output:
                _strip_page_images(page)

        output.save(
            str(target_path),
            garbage=4,
            clean=True,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
        )
    finally:
        output.close()
        src.close()

    return str(target_path)


def prepare_pdf_for_ai(input_pdf_path: str) -> str:
    """
    기본 추출용으로는 텍스트 레이어를 유지한 채 PDF를 정리해 둔다.
    원문 텍스트 추출은 여전히 원본 PDF 기준으로 진행된다.
    """
    return build_ai_ready_pdf(input_pdf_path, strip_images=False, suffix="optimized")


def build_revalidation_pdf(input_pdf_path: str, pages: Iterable[int]) -> str:
    """
    오류 재검증용으로는 필요한 페이지만 추리고 이미지까지 제거한 경량 PDF를 만든다.
    """
    return build_ai_ready_pdf(input_pdf_path, pages=list(pages), strip_images=True, suffix="revalidation")
