"""Identidade visual do evento: leitura do YAML simplificado, personalização pelo painel e theme.css.

O YAML (config/evento.yaml) é o padrão. A coordenação pode trocar nomes, instrução da busca, cores
e logo pelo painel: isso fica no banco (app_settings "event_theme" e tabela event_assets), entra
nos backups e vale por cima do YAML até alguém restaurar o padrão."""
import base64
import binascii
import hashlib
import json
import re

from . import settings
from .common import now
from .db import connect, get_setting, set_setting


COLOR_NAMES = ("primary", "navigation", "text", "text_muted",
               "action", "action_hover", "focus", "page_background", "surface")

COLOR_ALIASES = {
    "navy": "primary",
    "navy_header": "navigation",
    "ink": "text",
    "slate": "text_muted",
    "leaf": "action",
    "leaf_dark": "action_hover",
    "gold": "focus",
    "fog": "page_background",
    "white": "surface",
}


def read_event_yaml(path):
    """Parse the small mapping-only YAML format used by the event theme."""
    result = {}
    section = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"(  )?([A-Za-z_]+):(?: (.*))?", raw)
        if not match:
            raise ValueError(f"linha {number}: use chaves e recuo de dois espaços")
        nested, key, value = match.groups()
        if nested:
            if section is None:
                raise ValueError(f"linha {number}: seção ausente")
            target = result[section]
        else:
            target = result
        if key in target:
            raise ValueError(f"linha {number}: chave duplicada {key}")
        if value is None:
            if nested or key not in ("fonts", "colors"):
                raise ValueError(f"linha {number}: seção inválida")
            target[key] = {}
            section = key
        else:
            if value.startswith('"'):
                quoted = re.fullmatch(r'("(?:\\.|[^"\\])*")(?:\s+#.*)?', value)
                if not quoted:
                    raise ValueError(f"linha {number}: texto entre aspas inválido")
                value = json.loads(quoted.group(1))
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            elif " #" in value:
                value = value.split(" #", 1)[0]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"linha {number}: valor vazio")
            target[key] = value
            if not nested:
                section = None
    return result


HINTS = ("registration_hint_en", "registration_hint_pt")


def validate_identity(config):
    """Nomes, instrução da busca e cores: as regras valem para o YAML e para o painel."""
    for key in ("name", "short_name"):
        name = config[key]
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError(key)
    if any(key in config for key in HINTS) and not all(key in config for key in HINTS):
        raise ValueError("registration_hints")
    for key in HINTS:
        if key in config and (not isinstance(config[key], str) or not config[key].strip() or len(config[key]) > 300):
            raise ValueError(key)
    if not isinstance(config["colors"], dict) or set(config["colors"]) != set(COLOR_NAMES):
        raise ValueError("colors")
    for value in config["colors"].values():
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("color")


def file_config():
    """Read and validate public event appearance without exposing server settings."""
    try:
        config = read_event_yaml(settings.EVENT_CONFIG)
        validate_identity(config)
        for key, suffixes in (("logo", (".png", ".jpg", ".jpeg", ".webp", ".svg")),
                              ("body", (".woff2",)), ("display", (".woff2",))):
            value = config["fonts"][key] if key in ("body", "display") else config[key]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or not value.lower().endswith(suffixes):
                raise ValueError(key)
            if not (settings.STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
        if "decoration" in config:
            value = config["decoration"]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or not value.lower().endswith(".svg"):
                raise ValueError("decoration")
            if not (settings.STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuração visual inválida: {settings.EVENT_CONFIG}: {exc}") from exc
    return config


def event_config():
    """Identidade em uso: o YAML com o que a coordenação personalizou no painel por cima."""
    config = file_config()
    with connect() as db:
        custom = get_setting(db, "event_theme")
        logo = custom and custom.get("logo")
        if logo and not db.execute("SELECT 1 FROM event_assets WHERE name=?", (logo,)).fetchone():
            logo = None
    if custom:
        for key in ("name", "short_name", "colors"):
            config[key] = custom[key]
        for key in HINTS:
            config.pop(key, None)
            if key in custom:
                config[key] = custom[key]
        if logo:
            config["logo"] = logo
    return config


# --- Personalização pelo painel ---------------------------------------------------------------

UPLOAD_PREFIX = "upload-"
MAX_LOGO_BYTES = 1_000_000
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}
# Um SVG pode carregar código; a logo enviada não pode ter nada disso.
UNSAFE_SVG = re.compile(r"<script|<foreignobject|<iframe|<embed|<object|<!entity|javascript:|\bon[a-z]+\s*=",
                        re.IGNORECASE)


def theme_settings():
    """O que o formulário do painel mostra: valores em uso e o padrão do YAML."""
    default, current = file_config(), event_config()
    def public(config):
        values = {key: config.get(key, "") for key in ("name", "short_name", *HINTS)}
        values.update(colors=dict(config["colors"]), logo="/assets/" + config["logo"])
        return values
    with connect() as db:
        custom = get_setting(db, "event_theme")
    return {"current": public(current), "default": public(default), "customized": bool(custom),
            "custom_logo": current["logo"].startswith(UPLOAD_PREFIX)}


def save_theme(data, actor):
    values = {key: " ".join(str(data.get(key, "")).split()) for key in ("name", "short_name", *HINTS)}
    hints = [key for key in HINTS if values[key]]
    if len(hints) == 1:
        raise ValueError("Preencha a instrução da busca nos dois idiomas, ou deixe as duas vazias.")
    for key in HINTS:
        if not values[key]:
            del values[key]
    colors = data.get("colors")
    values["colors"] = {key: str(colors.get(key, "")).lower() for key in COLOR_NAMES} if isinstance(colors, dict) else None
    try:
        validate_identity(values)
    except (KeyError, TypeError, ValueError) as exc:
        field = {"name": "o nome do evento (até 100 caracteres)", "short_name": "o nome curto (até 100 caracteres)",
                 "colors": "todas as cores", "color": "as cores no formato #RRGGBB"}.get(str(exc), "a instrução da busca (até 300 caracteres)")
        raise ValueError(f"Verifique {field}.") from exc
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        values["logo"] = (get_setting(db, "event_theme") or {}).get("logo")
        values.update(updated_by=actor, updated_at=now())
        set_setting(db, "event_theme", values)
    print(f"Identidade visual alterada por {actor}.", flush=True)


def logo_type(data):
    """Extensão da imagem, conferida pelo conteúdo (não pelo nome do arquivo)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if re.search(r"<svg[\s>]", text) and text.lstrip().startswith("<"):
        if UNSAFE_SVG.search(text):
            raise ValueError("Este SVG tem código ou conteúdo externo. Exporte a logo de novo (como imagem simples) ou use PNG.")
        return "svg"
    return None


def save_logo(encoded, actor):
    try:
        data = base64.b64decode(str(encoded), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Arquivo inválido.") from exc
    if not data or len(data) > MAX_LOGO_BYTES:
        raise ValueError("A logo deve ter até 1 MB.")
    kind = logo_type(data)
    if not kind:
        raise ValueError("Use uma imagem PNG, JPEG, WebP ou SVG.")
    name = f"{UPLOAD_PREFIX}logo-{hashlib.sha256(data).hexdigest()[:16]}.{kind}"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        custom = get_setting(db, "event_theme") or theme_from(file_config())
        db.execute("INSERT OR REPLACE INTO event_assets(name,content_type,data,updated_at) VALUES (?,?,?,?)",
                   (name, LOGO_TYPES[kind], data, now()))
        db.execute("DELETE FROM event_assets WHERE name<>?", (name,))
        custom.update(logo=name, updated_by=actor, updated_at=now())
        set_setting(db, "event_theme", custom)
    print(f"Logo do evento trocada por {actor}.", flush=True)
    return "/assets/" + name


def theme_from(config):
    values = {key: config[key] for key in ("name", "short_name", *HINTS) if key in config}
    values["colors"] = dict(config["colors"])
    return values


def remove_logo(actor):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        custom = get_setting(db, "event_theme")
        if custom:
            custom.update(logo=None, updated_by=actor, updated_at=now())
            set_setting(db, "event_theme", custom)
        db.execute("DELETE FROM event_assets")
    print(f"Logo padrão restaurada por {actor}.", flush=True)


def reset_theme(actor):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM app_settings WHERE key='event_theme'")
        db.execute("DELETE FROM event_assets")
    print(f"Identidade visual padrão restaurada por {actor}.", flush=True)


def uploaded_asset(name):
    with connect() as db:
        return db.execute("SELECT content_type, data FROM event_assets WHERE name=?", (name,)).fetchone()


def theme_css(config):
    colors = "\n".join(f"  --{key.replace('_', '-')}: {config['colors'][key]};" for key in COLOR_NAMES)
    aliases = "\n".join(f"  --{alias.replace('_', '-')}: var(--{name.replace('_', '-')});"
                        for alias, name in COLOR_ALIASES.items())
    body = config["fonts"]["body"]
    display = config["fonts"]["display"]
    decoration = config.get("decoration")
    decoration_css = f'url("/assets/{decoration}")' if decoration else "none"
    decoration_opacity = ".13" if decoration else "0"
    return (f'@font-face {{ font-family: "Event Body"; src: url("/assets/{body}") format("woff2"); font-style: normal; font-weight: 100 900; font-display: swap; }}\n'
            f'@font-face {{ font-family: "Event Display"; src: url("/assets/{display}") format("woff2"); font-style: normal; font-weight: 400; font-display: swap; }}\n'
            f':root {{\n{colors}\n{aliases}\n  --event-decoration: {decoration_css};\n  --event-decoration-opacity: {decoration_opacity};\n}}\n')
