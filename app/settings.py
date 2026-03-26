from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
CACHE_DIR = BASE_DIR / "cache"

MASTER_XLSX_PATH = Path(os.getenv("MASTER_XLSX_PATH", "")).expanduser() if os.getenv("MASTER_XLSX_PATH") else (DATA_DIR / "master.xlsx")
MASTER_META_PATH = DATA_DIR / "master_meta.json"
INSPECTION_LOG_PATH = DATA_DIR / "inspection_error_logs.jsonl"
LAST_INSPECTION_STATE_PATH = CACHE_DIR / "last_inspection_state.json"
LAST_INSPECTION_PDF_DIR = CACHE_DIR / "last_inspection_pdfs"
HOST = os.getenv("HOST", "127.0.0.1").strip()
PORT = int(os.getenv("PORT", "8000"))
RELOAD = os.getenv("RELOAD", "true").lower() == "true"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "120"))
OPENAI_FILE_EXPIRE_SECONDS = int(os.getenv("OPENAI_FILE_EXPIRE_SECONDS", "1800"))
PDF_TEXT_SNIPPET_LIMIT = int(os.getenv("PDF_TEXT_SNIPPET_LIMIT", "2200"))

DATA_DIR.mkdir(exist_ok=True, parents=True)
TEMP_DIR.mkdir(exist_ok=True, parents=True)
CACHE_DIR.mkdir(exist_ok=True, parents=True)
LAST_INSPECTION_PDF_DIR.mkdir(exist_ok=True, parents=True)
