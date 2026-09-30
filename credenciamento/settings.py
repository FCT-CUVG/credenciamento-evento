"""Caminhos e variáveis de ambiente.

Os outros módulos leem estes valores como settings.NOME no momento do uso, para que os testes
possam trocá-los (por exemplo, apontar DB e RANGES para uma pasta temporária)."""
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CHECKIN_DATA_DIR", ROOT / "data"))
DB = DATA / "credenciamento.sqlite3"
CSV = DATA / "participantes.csv"
STATIC = ROOT / "static"
GOOGLE_SHEETS_SCRIPT = ROOT / "google-sheets" / "Code.gs"
RANGES = Path(os.environ.get("CHECKIN_RANGES_FILE", ROOT / "config" / "guiches.json"))
DEFAULT_EVENT_CONFIG = ROOT / "config" / "evento.yaml"
EXAMPLE_EVENT_CONFIG = ROOT / "config" / "evento.example.yaml"
EVENT_CONFIG = Path(os.environ.get(
    "CHECKIN_EVENT_CONFIG",
    DEFAULT_EVENT_CONFIG if DEFAULT_EVENT_CONFIG.exists() else EXAMPLE_EVENT_CONFIG,
))
SECRET = os.environ.get("CHECKIN_SESSION_SECRET", "").encode()
SHEET_URL = os.environ.get("CHECKIN_SHEETS_URL", "")
SHEET_SECRET = os.environ.get("CHECKIN_SHEETS_SECRET", "")
PUBLIC_URL = os.environ.get("CHECKIN_PUBLIC_URL", "")
TRUST_PROXY = os.environ.get("CHECKIN_TRUSTED_PROXY", "") == "1"
