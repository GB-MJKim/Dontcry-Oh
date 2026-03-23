from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


class Settings(BaseModel):
    app_name: str = 'PDF 가격 검수기 (로컬 AI 모드)'
    openai_api_key: str = os.getenv('OPENAI_API_KEY', '')
    openai_model: str = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
    max_parallel_cards: int = int(os.getenv('MAX_PARALLEL_CARDS', '4'))
    ai_enabled: bool = os.getenv('AI_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'y'}
    host: str = os.getenv('HOST', '127.0.0.1')
    port: int = int(os.getenv('PORT', '8000'))
    master_excel_path: Path = BASE_DIR / 'data' / 'master.xlsx'
    cache_dir: Path = BASE_DIR / 'cache'
    temp_dir: Path = BASE_DIR / 'temp'


settings = Settings()
settings.cache_dir.mkdir(exist_ok=True, parents=True)
settings.temp_dir.mkdir(exist_ok=True, parents=True)
