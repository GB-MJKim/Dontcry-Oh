import os, tempfile, shutil
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .settings import TEMP_DIR
from . import data_manager
from .pdf_parser import parse_pdf
from .ai_parser import maybe_refine_item_with_ai
from .comparator import compare_items

app = FastAPI(title="로컬 가격검수 서비스")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

REGIONS = ["수도권", "경상권", "경남부산", "호남권"]

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    df = data_manager.load_master_df()
    summary = data_manager.summarize_master_df(df)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "regions": REGIONS,
        "master_count": summary["count"],
        "columns": summary["columns"],
        "rows": summary["rows"][:30],
        "results": None,
    })

@app.post("/inspect", response_class=HTMLResponse)
async def inspect_pdf(request: Request, region: str = Form(...), pdf_file: UploadFile = File(...)):
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp_path = os.path.join(TEMP_DIR, pdf_file.filename)
    with open(tmp_path, "wb") as f:
        f.write(await pdf_file.read())

    master_df = data_manager.load_master_df()
    items = parse_pdf(tmp_path)
    refined = [maybe_refine_item_with_ai(item) for item in items]
    results = compare_items(refined, master_df, region)
    summary = data_manager.summarize_master_df(master_df)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "regions": REGIONS,
        "master_count": summary["count"],
        "columns": summary["columns"],
        "rows": summary["rows"][:30],
        "results": results,
        "selected_region": region,
        "uploaded_pdf_name": pdf_file.filename,
    })

@app.post("/data/upload")
async def upload_master(file: UploadFile = File(...)):
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(await file.read())
    data_manager.save_master_excel(tmp)
    os.remove(tmp)
    return RedirectResponse(url="/", status_code=303)

@app.get("/data/json")
def data_json():
    df = data_manager.load_master_df()
    return JSONResponse(data_manager.summarize_master_df(df))
