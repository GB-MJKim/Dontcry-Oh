import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import data_manager
from .comparator import compare_items, inspect_items
from .pdf_parser import parse_pdf
from .revalidator import revalidate_error_items
from .settings import OPENAI_MODEL, TEMP_DIR

app = FastAPI(title="Don't Cry, Oh!")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

REGIONS = ["수도권", "경상권", "경남부산", "호남권"]


def _base_context(request: Request) -> dict:
    master_rows = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(master_rows)
    return {
        "request": request,
        "regions": REGIONS,
        "master_count": summary["count"],
        "columns": summary["columns"],
        "rows": summary["rows"][:20],
        "results": None,
        "error_message": None,
        "success_message": None,
        "parsed_count": 0,
        "selected_region": "수도권",
        "uploaded_pdf_name": None,
        "openai_model": OPENAI_MODEL,
    }


def render_home(request: Request, **kwargs):
    context = _base_context(request)
    context["active_page"] = "home"
    context.update(kwargs)
    return templates.TemplateResponse("index.html", context)


def render_master(request: Request, **kwargs):
    context = _base_context(request)
    context["active_page"] = "master"
    context.update(kwargs)
    return templates.TemplateResponse("master.html", context)


def _build_inspection_message(results, review_summary):
    if not results:
        return "상품을 찾지 못했습니다."

    parts = ["검수가 완료되었습니다."]
    attempted = int(review_summary.get("attempted") or 0)
    updated_items = int(review_summary.get("updated_items") or 0)
    if review_summary.get("error"):
        parts.append("2차 AI 재검증은 수행하지 못해 1차 결과를 표시합니다.")
    elif attempted:
        parts.append(f"오류 {attempted}건을 2차 AI로 재검증했습니다.")
        if updated_items:
            parts.append(f"{updated_items}건에 수정값을 반영했습니다.")
        else:
            parts.append("추가로 반영할 수정값은 없었습니다.")
    return " ".join(parts)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render_home(request)


@app.get("/master", response_class=HTMLResponse)
def master_page(request: Request):
    return render_master(request)


@app.post("/data/upload", response_class=HTMLResponse)
async def upload_master(
    request: Request,
    file: UploadFile = File(...),
    return_page: str = Form("master"),
):
    render = render_master if return_page == "master" else render_home
    try:
        if not file.filename.lower().endswith((".xlsx", ".xlsm")):
            return render(request, error_message="엑셀 파일(.xlsx/.xlsm)만 업로드할 수 있습니다.")
        temp_path = Path(TEMP_DIR) / "_uploaded_master.xlsx"
        with open(temp_path, "wb") as handle:
            handle.write(await file.read())
        data_manager.save_master_excel(str(temp_path))
        return render(request, success_message="기준 엑셀을 교체했습니다.")
    except Exception as exc:
        return render(request, error_message=f"기준 엑셀 업로드 중 오류가 발생했습니다: {exc}")


@app.post("/inspect", response_class=HTMLResponse)
async def inspect_pdf(
    request: Request,
    region: str = Form(...),
    pdf_file: UploadFile = File(...),
):
    try:
        if region not in REGIONS:
            return render_home(request, error_message="권역 값이 올바르지 않습니다.")
        if not pdf_file.filename.lower().endswith(".pdf"):
            return render_home(request, error_message="PDF 파일만 업로드할 수 있습니다.", selected_region=region)

        master_rows = data_manager.load_master_df()
        if not master_rows:
            return render_home(
                request,
                error_message="기준 엑셀 파일이 없습니다. 먼저 기준 엑셀을 업로드해 주세요.",
                selected_region=region,
            )

        safe_name = os.path.basename(pdf_file.filename)
        pdf_path = Path(TEMP_DIR) / safe_name
        with open(pdf_path, "wb") as handle:
            handle.write(await pdf_file.read())

        items = parse_pdf(str(pdf_path), master_rows)
        review_summary = {"attempted": 0, "updated_items": 0, "updated_fields": 0, "error": None}
        if items:
            initial_contexts = inspect_items(items, master_rows, region)
            review_summary = revalidate_error_items(items, initial_contexts, region)

        results = compare_items(items, master_rows, region)
        success_message = _build_inspection_message(results, review_summary)

        return render_home(
            request,
            selected_region=region,
            results=results,
            parsed_count=len(items),
            uploaded_pdf_name=safe_name,
            success_message=success_message,
        )
    except RuntimeError as exc:
        return render_home(
            request,
            selected_region=region,
            error_message=str(exc),
        )
    except Exception as exc:
        return render_home(
            request,
            selected_region=region,
            error_message=f"검수 중 오류가 발생했습니다: {exc}",
        )
