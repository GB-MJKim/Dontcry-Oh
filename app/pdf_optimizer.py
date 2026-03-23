import os
import io
import fitz
from PIL import Image
from typing import Dict, List, Tuple

# 페이지별 판단 기준
MAX_IMAGE_PIXEL_AREA = 2_000_000
JPEG_QUALITY_BALANCED = 35
JPEG_QUALITY_AGGRESSIVE = 20
DOWNSAMPLE_MAX_SIDE = 1280
VERY_BRIGHT_TEXT_THRESHOLD = 230

def has_text_layer(pdf_path: str, sample_pages: int = 3) -> bool:
    doc = fitz.open(pdf_path)
    for i in range(min(sample_pages, len(doc))):
        if doc[i].get_text("text").strip():
            return True
    return False

def _page_text_stats(page) -> Dict:
    raw = page.get_text("rawdict")
    span_count = 0
    bright_span_count = 0
    text_chars = 0
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = "".join(ch.get("c", "") for ch in span.get("chars", []))
                if not txt.strip():
                    continue
                span_count += 1
                text_chars += len(txt.strip())
                color = span.get("color", 0)
                r = (color >> 16) & 255
                g = (color >> 8) & 255
                b = color & 255
                if r >= VERY_BRIGHT_TEXT_THRESHOLD and g >= VERY_BRIGHT_TEXT_THRESHOLD and b >= VERY_BRIGHT_TEXT_THRESHOLD:
                    bright_span_count += 1
    return {
        "span_count": span_count,
        "bright_span_count": bright_span_count,
        "text_chars": text_chars,
        "bright_ratio": round(bright_span_count / span_count, 3) if span_count else 0.0,
    }

def _page_image_xrefs(page) -> List[int]:
    imgs = []
    for info in page.get_images(full=True):
        if info and len(info) > 0:
            imgs.append(info[0])
    return list(dict.fromkeys(imgs))

def _image_meta(doc, xref: int) -> Dict:
    try:
        info = doc.extract_image(xref)
    except Exception:
        return {"xref": xref, "ok": False}
    return {
        "xref": xref,
        "ok": True,
        "ext": info.get("ext"),
        "width": info.get("width", 0),
        "height": info.get("height", 0),
        "size": len(info.get("image", b"")),
    }

def analyze_pdf_pages(pdf_path: str) -> Dict:
    doc = fitz.open(pdf_path)
    pages = []
    has_any_text = False
    for idx, page in enumerate(doc):
        t = _page_text_stats(page)
        if t["text_chars"] > 0:
            has_any_text = True
        image_xrefs = _page_image_xrefs(page)
        metas = [_image_meta(doc, x) for x in image_xrefs]
        large_images = sum(1 for m in metas if m.get("ok") and m.get("width", 0) * m.get("height", 0) >= MAX_IMAGE_PIXEL_AREA)
        image_bytes = sum(m.get("size", 0) for m in metas if m.get("ok"))
        pages.append({
            "page_number": idx + 1,
            "text_chars": t["text_chars"],
            "span_count": t["span_count"],
            "bright_ratio": t["bright_ratio"],
            "image_count": len(image_xrefs),
            "large_image_count": large_images,
            "image_bytes": image_bytes,
            "remove_safe": bool(t["text_chars"] > 0 and t["bright_ratio"] < 0.08),
            "notes": [],
        })
    return {"has_text_layer": has_any_text, "pages": pages}

def _downsample_and_jpeg(data: bytes, quality: int, max_side: int) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h)) if max(w, h) else 1.0
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()

def _replace_image(doc, xref: int, new_bytes: bytes):
    try:
        doc.update_stream(xref, new_bytes)
    except Exception:
        pass

def _drop_page_images(page):
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            page.delete_image(xref)
        except Exception:
            # 일부 PDF에서는 삭제가 제한될 수 있어 무시
            pass

def optimize_pdf(input_path: str, output_path: str, mode: str = "auto") -> Dict:
    analysis = analyze_pdf_pages(input_path)
    original_size = os.path.getsize(input_path)

    # 1차: 구조 정리 기본 저장
    doc = fitz.open(input_path)

    page_actions = []
    for page_info in analysis["pages"]:
        page_no = page_info["page_number"]
        page = doc[page_no - 1]
        action = "safe"

        if mode == "safe":
            action = "safe"
        elif mode == "balanced":
            action = "compress"
        elif mode == "text_priority":
            action = "remove" if page_info["remove_safe"] else "compress"
        else:  # auto
            if page_info["remove_safe"] and page_info["image_count"] > 0:
                action = "remove"
            elif page_info["image_count"] > 0:
                action = "compress"
            else:
                action = "safe"

        if action == "remove":
            _drop_page_images(page)
        elif action == "compress":
            for xref in list(dict.fromkeys([img[0] for img in page.get_images(full=True)])):
                try:
                    info = doc.extract_image(xref)
                    if not info or not info.get("image"):
                        continue
                    new_bytes = _downsample_and_jpeg(
                        info["image"],
                        quality=JPEG_QUALITY_BALANCED if mode != "text_priority" else JPEG_QUALITY_AGGRESSIVE,
                        max_side=DOWNSAMPLE_MAX_SIDE,
                    )
                    if len(new_bytes) < len(info["image"]):
                        _replace_image(doc, xref, new_bytes)
                except Exception:
                    continue
        page_actions.append({"page": page_no, "action": action})

    # 2차: 저장 최적화
    try:
        doc.save(output_path, garbage=4, clean=True, deflate=True, use_objstms=1)
    except TypeError:
        doc.save(output_path, garbage=4, clean=True, deflate=True)

    optimized_size = os.path.getsize(output_path)
    return {
        "original_bytes": original_size,
        "optimized_bytes": optimized_size,
        "reduction_bytes": max(0, original_size - optimized_size),
        "reduction_ratio": round((1 - optimized_size / original_size) * 100, 2) if original_size else 0.0,
        "mode": mode,
        "has_text_layer": analysis["has_text_layer"],
        "page_actions": page_actions,
        "page_analysis": analysis["pages"],
    }
