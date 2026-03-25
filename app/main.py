import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import data_manager
from .comparator import compare_items
from .pdf_parser import parse_pdf
from .settings import TEMP_DIR, OPENAI_MODEL

app = FastAPI(title="홍보북 가격 검수")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

REGIONS = ["수도권", "경상권", "경남부산", "호남권"]


def render_home(request: Request, **kwargs):
    master_rows = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(master_rows)
    context = {
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
        "openai_model": OPENAI_MODEL,
    }
    context.update(kwargs)
    return templates.TemplateResponse("index.html", context)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render_home(request)


@app.post("/data/upload", response_class=HTMLResponse)
async def upload_master(request: Request, file: UploadFile = File(...)):
    try:
        if not file.filename.lower().endswith((".xlsx", ".xlsm")):
            return render_home(request, error_message="엑셀 파일(.xlsx/.xlsm)만 업로드할 수 있습니다.")
        temp_path = Path(TEMP_DIR) / "_uploaded_master.xlsx"
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        data_manager.save_master_excel(str(temp_path))
        return render_home(request, success_message="기준 엑셀을 교체했습니다.")
    except Exception as exc:
        return render_home(request, error_message=f"기준 엑셀 업로드 중 오류가 발생했습니다: {exc}")


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
            return render_home(request, error_message="기준 엑셀 파일이 없습니다. 먼저 기준 엑셀을 업로드해 주세요.", selected_region=region)

        safe_name = os.path.basename(pdf_file.filename)
        pdf_path = Path(TEMP_DIR) / safe_name
        with open(pdf_path, "wb") as f:
            f.write(await pdf_file.read())

        items = parse_pdf(str(pdf_path), master_rows)
        results = compare_items(items, master_rows, region)

        return render_home(
            request,
            selected_region=region,
            results=results,
            parsed_count=len(items),
            uploaded_pdf_name=safe_name,
            success_message="검수가 완료되었습니다." if results else "상품을 찾지 못했습니다.",
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
