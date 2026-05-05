from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH, override=False)

DB_PATH = Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "databridge.db"))).resolve()
HF_TOKEN = os.getenv("HF_TOKEN")
HF_NAMESPACE = (os.getenv("HF_NAMESPACE") or "").strip()
HF_DATASET_VISIBILITY = (os.getenv("HF_DATASET_VISIBILITY") or "public").strip().lower()
REMOTE_PROVIDER = "huggingface"
API_TITLE = "Richat DataBridge API"


def hf_is_configured() -> bool:
    return bool(HF_TOKEN and HF_NAMESPACE)
