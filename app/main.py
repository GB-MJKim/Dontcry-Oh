from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil
import os

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .excel_matcher import MasterCatalog
from .pdf_cropper import detect_cards
from .ai_parser import AIProductParser
from .comparison import compare_products

BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title=settings.app_name)
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'app' / 'static')), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'app' / 'templates'))

catalog = MasterCatalog(settings.master_excel_path)
ai_parser = AIProductParser()


@app.get('/', response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse('index.html', {
        'request': request,
        'app_name': settings.app_name,
        'regions': ['수도권', '경상권', '경남 부산', '호남권'],
        'model_name': settings.openai_model,
        'ai_enabled': ai_parser.enabled,
    })


@app.get('/healthz')
async def healthz():
    return {
        'ok': True,
        'time': datetime.now().isoformat(timespec='seconds'),
        'ai_enabled': ai_parser.enabled,
        'model': settings.openai_model,
        'master_excel': str(settings.master_excel_path),
    }


@app.post('/analyze', response_class=HTMLResponse)
async def analyze(request: Request, region: str = Form(...), pdf_file: UploadFile = File(...)):
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = settings.temp_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = run_dir / pdf_file.filename
    with open(pdf_path, 'wb') as f:
        shutil.copyfileobj(pdf_file.file, f)

    cards = detect_cards(pdf_path, run_dir / 'cards')
    parsed = ai_parser.parse_cards(cards)
    results = compare_products(parsed, catalog, region)

    summary = {
        'total': len(results),
        'ok': sum(1 for r in results if r.status == 'ok'),
        'discount_applied': sum(1 for r in results if r.status == 'discount_applied'),
        'warning': sum(1 for r in results if r.status == 'warning'),
        'error': sum(1 for r in results if r.status == 'error'),
    }

    return templates.TemplateResponse('result.html', {
        'request': request,
        'app_name': settings.app_name,
        'file_name': pdf_file.filename,
        'region': region,
        'summary': summary,
        'results': results,
        'run_id': run_id,
        'ai_enabled': ai_parser.enabled,
        'model_name': settings.openai_model,
    })
