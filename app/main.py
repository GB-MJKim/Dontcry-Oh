import os
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .settings import TEMP_DIR, OPENAI_API_KEY, OPENAI_MODEL, PDF_OPTIMIZE_MODE
from . import data_manager
from .pdf_optimizer import optimize_pdf
from .gpt_pdf_parser import parse_pdf_with_gpt
from .comparator import compare_items

app = FastAPI(title="로컬 가격검수 서비스")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

REGIONS = ["수도권", "경상권", "경남부산", "호남권"]
REQUEST_FILE_LIMIT_BYTES = 50 * 1024 * 1024

def render_home(request: Request, **kwargs):
    df = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(df)
    fmap = data_manager.get_field_map(df) if not df.empty else {}
    context = {
        "request": request,
        "regions": REGIONS,
        "master_count": summary["count"],
        "columns": summary["columns"],
        "rows": summary["rows"][:30],
        "field_map": fmap,
        "openai_ready": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_MODEL,
        "default_optimize_mode": PDF_OPTIMIZE_MODE,
        "results": None,
    }
    context.update(kwargs)
    return templates.TemplateResponse("index.html", context)

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render_home(request)

@app.post("/inspect", response_class=HTMLResponse)
async def inspect_pdf(
    request: Request,
    region: str = Form(...),
    optimize_mode: str = Form("auto"),
    pdf_file: UploadFile = File(...),
):
    os.makedirs(TEMP_DIR, exist_ok=True)
    original_path = os.path.join(TEMP_DIR, pdf_file.filename)
    optimized_path = os.path.join(TEMP_DIR, f"optimized_{pdf_file.filename}")
    with open(original_path, "wb") as f:
        f.write(await pdf_file.read())

    optimization = optimize_pdf(original_path, optimized_path, optimize_mode)

    if os.path.getsize(optimized_path) > REQUEST_FILE_LIMIT_BYTES:
        return render_home(
            request,
            error_message="최적화 후 PDF 크기가 50MB를 초과합니다. 압축 모드를 text_priority로 바꾸거나 PDF를 분할해 주세요.",
            optimization=optimization,
            selected_region=region,
            uploaded_pdf_name=pdf_file.filename,
            selected_optimize_mode=optimize_mode,
        )

    if not OPENAI_API_KEY:
        return render_home(
            request,
            error_message="OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.",
            optimization=optimization,
            selected_region=region,
            uploaded_pdf_name=pdf_file.filename,
            selected_optimize_mode=optimize_mode,
        )

    master_df = data_manager.load_master_df()
    try:
        items = parse_pdf_with_gpt(optimized_path)
    except Exception as e:
        return render_home(
            request,
            error_message=f"GPT PDF 파싱 중 오류가 발생했습니다: {e}",
            optimization=optimization,
            selected_region=region,
            uploaded_pdf_name=pdf_file.filename,
            selected_optimize_mode=optimize_mode,
        )

    results = compare_items(items, master_df, region)
    return render_home(
        request,
        results=results,
        selected_region=region,
        uploaded_pdf_name=pdf_file.filename,
        optimization=optimization,
        parsed_count=len(items),
        selected_optimize_mode=optimize_mode,
    )

@app.post("/data/upload")
async def upload_master(file: UploadFile = File(...)):
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp = os.path.join(TEMP_DIR, "_master_upload.xlsx")
    with open(tmp, "wb") as f:
        f.write(await file.read())
    data_manager.save_master_excel(tmp)
    return RedirectResponse(url="/", status_code=303)

@app.get("/data/json")
def data_json():
    df = data_manager.load_master_df()
    return JSONResponse(data_manager.summarize_master_df(df))
