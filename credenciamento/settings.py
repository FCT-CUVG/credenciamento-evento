"""Caminhos e variáveis de ambiente.

Os outros módulos leem estes valores como settings.NOME no momento do uso, para que os testes
possam trocá-los (por exemplo, apontar DB e RANGES para uma pasta temporária)."""
import ipaddress
import os
import re
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
# Chave das buscas por CPF e e-mail, que só ficam guardados como HMAC. Se mudar, é preciso reimportar a lista.
LOOKUP_SECRET = os.environ.get("CHECKIN_LOOKUP_SECRET", "").encode()
SHEET_URL = os.environ.get("CHECKIN_SHEETS_URL", "")
SHEET_SECRET = os.environ.get("CHECKIN_SHEETS_SECRET", "")
PUBLIC_URL = os.environ.get("CHECKIN_PUBLIC_URL", "")
TRUSTED_PROXY = os.environ.get("CHECKIN_TRUSTED_PROXY", "")
# Limites de tentativas no formato "quantidade/minutos".
LIMITS = {
    "search": os.environ.get("CHECKIN_LIMIT_SEARCH", "60/1"),             # buscas por IP
    "search_miss": os.environ.get("CHECKIN_LIMIT_SEARCH_MISS", "20/10"),  # buscas sem resultado por IP
    "login_ip": os.environ.get("CHECKIN_LIMIT_LOGIN_IP", "20/5"),         # senhas erradas por IP
    "login_user": os.environ.get("CHECKIN_LIMIT_LOGIN_USER", "10/15"),    # senhas erradas por conta
    "alert_miss": os.environ.get("CHECKIN_ALERT_SEARCH_MISS", "30/10"),   # alerta no painel (todas as origens)
}


def trusted_proxy_networks(value=None):
    """Endereços de onde se aceita X-Forwarded-For: "1" confia só na própria máquina; também
    aceita IPs ou redes separados por vírgula ou espaço (ex.: o proxy HTTPS da instituição)."""
    value = TRUSTED_PROXY if value is None else value
    if value.strip() == "1":
        value = "127.0.0.1,::1"
    return tuple(ipaddress.ip_network(item, strict=False) for item in re.split(r"[,\s]+", value) if item)


def rate_limit(name):
    """Devolve (quantidade, janela em segundos) do limite configurado em LIMITS."""
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", LIMITS[name])
    if not match or not int(match[1]) or not int(match[2]):
        raise ValueError(f"Limite {name} inválido: use quantidade/minutos, por exemplo 20/10.")
    return int(match[1]), int(match[2]) * 60
