from dotenv import load_dotenv
import os

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
OPENAI_FILE_EXPIRE_SECONDS = int(os.getenv("OPENAI_FILE_EXPIRE_SECONDS", "86400"))
HOST = os.getenv("HOST", "127.0.0.1").strip()
PORT = int(os.getenv("PORT", "8000"))
RELOAD = os.getenv("RELOAD", "true").lower() == "true"
PDF_OPTIMIZE_MODE = os.getenv("PDF_OPTIMIZE_MODE", "auto").strip().lower()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
MASTER_XLSX_PATH = os.path.join(DATA_DIR, "master.xlsx")
