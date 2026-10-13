"""Versão do sistema mostrada no painel: arquivo VERSION gravado no build da imagem ou, rodando de
um clone do repositório, o git describe. Releases usam tags vX.Y.Z (versionamento semântico)."""
import re
import subprocess
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNKNOWN = "desconhecida"


def _describe():
    path = ROOT / "VERSION"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    try:
        result = subprocess.run(["git", "describe", "--tags", "--always", "--match", "v[0-9]*"], cwd=ROOT,
                                capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def parse(described):
    """Separa a saída do git describe em tag, commits depois dela e commit curto."""
    described = described.strip()
    match = re.fullmatch(r"(v\d[\w.+-]*?)(?:-(\d+)-g([0-9a-f]+))?", described)
    if match:
        tag, ahead, commit = match.groups()
        return {"tag": tag, "ahead": int(ahead or 0), "commit": commit or ""}
    if re.fullmatch(r"[0-9a-f]{7,40}", described):
        return {"tag": "", "ahead": 0, "commit": described}
    return {"tag": "", "ahead": 0, "commit": ""}


def label(info):
    if info["tag"] and info["ahead"]:
        return f"{info['tag']} + {info['ahead']} commit{'s' if info['ahead'] > 1 else ''} ({info['commit']})"
    if info["tag"]:
        return info["tag"]
    if info["commit"]:
        return f"commit {info['commit']}"
    return UNKNOWN


@cache
def current():
    info = parse(_describe())
    return {**info, "label": label(info)}
