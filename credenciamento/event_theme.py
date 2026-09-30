"""Identidade visual do evento: leitura do YAML simplificado e geração do theme.css."""
import json
import re

from . import settings


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


def event_config():
    """Read and validate public event appearance without exposing server settings."""
    try:
        config = read_event_yaml(settings.EVENT_CONFIG)
        for key in ("name", "short_name"):
            name = config[key]
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError(key)
        hints = ("registration_hint_en", "registration_hint_pt")
        if any(key in config for key in hints) and not all(key in config for key in hints):
            raise ValueError("registration_hints")
        for key in hints:
            if key in config and (not isinstance(config[key], str) or not config[key].strip() or len(config[key]) > 300):
                raise ValueError(key)
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
        if set(config["colors"]) != set(COLOR_NAMES):
            raise ValueError("colors")
        for value in config["colors"].values():
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("color")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuração visual inválida: {settings.EVENT_CONFIG}: {exc}") from exc
    return config


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
