#!/usr/bin/env python3
"""Servidor de credenciamento sem dependências, com SQLite."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict, deque
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
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
RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
CSV_LOCK = threading.Lock()
RANGES_LOCK = threading.Lock()

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
        config = read_event_yaml(EVENT_CONFIG)
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
            if not (STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
        if "decoration" in config:
            value = config["decoration"]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or not value.lower().endswith(".svg"):
                raise ValueError("decoration")
            if not (STATIC / "assets" / value).is_file():
                raise ValueError(f"asset ausente: {value}")
        if set(config["colors"]) != set(COLOR_NAMES):
            raise ValueError("colors")
        for value in config["colors"].values():
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ValueError("color")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuração visual inválida: {EVENT_CONFIG}: {exc}") from exc
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


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").strip())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def mask_public_text(value):
    """Hide the middle of each word before returning participant data publicly."""
    def mask_word(match):
        word = match.group()
        return word[0] + "*" * (len(word) - 2) + word[-1] if len(word) > 2 else word

    return re.sub(r"[^\W_]+", mask_word, value or "", flags=re.UNICODE)


def read_desk_file():
    """Parsed guichês.json; callers that loop should read it once and pass the result along."""
    try:
        config = json.loads(RANGES.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Configuração de guichês inválida: {RANGES}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("ranges"), list):
        raise ValueError(f"Configuração de guichês inválida: {RANGES}")
    return config


def guiche_for(name, ranges=None):
    initial = normalize(name)[:1].upper()
    ranges = configured_ranges() if ranges is None else ranges
    matches = [str(r["guiche"]).strip() for r in ranges
               if str(r["from"]).upper() <= initial <= str(r["to"]).upper()]
    if len(matches) != 1 or not matches[0]:
        raise ValueError(f"Inicial {initial or '?'} de {name!r} sem faixa única de guichê.")
    return matches[0]


def configured_ranges():
    return read_desk_file()["ranges"]


DEFAULT_PRIORITY_GUICHE = "P"


def priority_guiche():
    """Desk for participants marked as priority; an empty value turns it off."""
    try:
        config = read_desk_file()
    except ValueError:
        return DEFAULT_PRIORITY_GUICHE
    value = str(config.get("priority_guiche", DEFAULT_PRIORITY_GUICHE) or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value) else ""


def validate_priority_guiche(value):
    value = str(value or "").strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value):
        raise ValueError("Guichê de prioridade: use até 24 letras, números, _ ou -.")
    return value


def auto_guiche(name, priority, priority_desk=None, ranges=None):
    """Desk computed from priority and name initial, without explicit assignment."""
    desk = priority_guiche() if priority_desk is None else priority_desk
    return desk if priority and desk else guiche_for(name, ranges)


def desk_ranges(guiche):
    """Letter ranges served by a desk; empty for desks assigned only explicitly."""
    try:
        ranges = configured_ranges()
    except ValueError:
        return []
    return [{"from": str(r.get("from", "")).strip().upper(), "to": str(r.get("to", "")).strip().upper()}
            for r in ranges if isinstance(r, dict) and str(r.get("guiche", "")).strip() == guiche]


def validate_ranges(ranges):
    if not isinstance(ranges, list) or not ranges or len(ranges) > 26:
        raise ValueError("Informe entre 1 e 26 faixas de letras.")
    normalized = []
    coverage = set()
    for index, item in enumerate(ranges, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Faixa {index}: dados inválidos.")
        start = str(item.get("from", "")).strip().upper()
        end = str(item.get("to", "")).strip().upper()
        desk = str(item.get("guiche", "")).strip()
        if not re.fullmatch(r"[A-Z]", start) or not re.fullmatch(r"[A-Z]", end) or start > end:
            raise ValueError(f"Faixa {index}: informe letras de A a Z em ordem.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", desk):
            raise ValueError(f"Faixa {index}: identificador de guichê inválido.")
        letters = set(chr(code) for code in range(ord(start), ord(end) + 1))
        if coverage & letters:
            raise ValueError(f"Faixa {index}: há letras atribuídas a mais de um guichê.")
        coverage.update(letters)
        normalized.append({"from": start, "to": end, "guiche": desk})
    missing = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") - coverage
    if missing:
        raise ValueError("Todas as letras de A a Z precisam ter um guichê. Faltam: " + ", ".join(sorted(missing)))
    return normalized


def save_ranges(ranges, priority=None, actor="system"):
    """Save desk configuration and move participants accordingly; returns how many changed."""
    normalized = validate_ranges(ranges)
    priority = priority_guiche() if priority is None else validate_priority_guiche(priority)
    old_priority = priority_guiche()
    try:
        old_ranges = validate_ranges(configured_ranges())
    except ValueError:
        old_ranges = []
    with RANGES_LOCK:
        temporary = RANGES.with_suffix(RANGES.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps({"ranges": normalized, "priority_guiche": priority},
                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, RANGES)
        finally:
            temporary.unlink(missing_ok=True)
    changed = apply_desk_config(old_ranges, old_priority, normalized, priority, actor)
    return normalized, priority, changed


def desk_renames(old_ranges, old_priority, new_ranges, new_priority):
    """Desks whose letter range (or priority role) stayed the same but got a new name."""
    new_names = {r["guiche"] for r in new_ranges} | ({new_priority} if new_priority else set())
    old_by_span = {(r["from"], r["to"]): r["guiche"] for r in old_ranges}
    renames, conflicts = {}, set()
    for r in new_ranges:
        old = old_by_span.get((r["from"], r["to"]))
        if old and old != r["guiche"]:
            if renames.setdefault(old, r["guiche"]) != r["guiche"]:
                conflicts.add(old)
    renames = {old: new for old, new in renames.items() if old not in conflicts and old not in new_names}
    if old_priority and new_priority and old_priority != new_priority and old_priority not in new_names:
        renames[old_priority] = new_priority
    return renames


def reconcile_priority_desk(db, ranges, priority_desk, actor):
    """Há um só guichê de prioridade. Um guichê fora da configuração em que todos têm prioridade
    é o guichê de prioridade com um nome antigo: todos (e os atendentes) passam para o nome atual."""
    if not priority_desk:
        return 0
    configured = {r["guiche"] for r in ranges} | {priority_desk}
    changed = 0
    for (desk,) in db.execute("SELECT DISTINCT guiche FROM participants").fetchall():
        if desk in configured:
            continue
        rows = db.execute("SELECT * FROM participants WHERE guiche=?", (desk,)).fetchall()
        if not all(row["priority"] for row in rows):
            continue
        for row in rows:
            update_participant(db, row["id"], "guiche_rename", actor, guiche=priority_desk, guiche_manual=0)
            changed += 1
        db.execute("UPDATE users SET guiche=? WHERE guiche=?", (priority_desk, desk))
    return changed


def apply_desk_config(old_ranges, old_priority, new_ranges, new_priority, actor):
    """Renamed desks follow everyone (the kit is at the same desk); other changes only move
    automatically assigned participants whose kit search has not started."""
    renames = desk_renames(old_ranges, old_priority, new_ranges, new_priority)
    changed = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for old, new in renames.items():
            for row in db.execute("SELECT id FROM participants WHERE guiche=?", (old,)).fetchall():
                update_participant(db, row["id"], "guiche_rename", actor, guiche=new)
                changed += 1
            db.execute("UPDATE users SET guiche=? WHERE guiche=?", (new, old))
        changed += reconcile_priority_desk(db, new_ranges, new_priority, actor)
        # Prioridade manda mesmo sobre guichê manual; os demais manuais ficam onde estão.
        for row in db.execute("SELECT * FROM participants WHERE (guiche_manual=0 OR priority=1) "
                              "AND status IN ('registered','prechecked')").fetchall():
            if row["guiche_manual"] and not (row["priority"] and new_priority):
                continue
            try:
                guiche = auto_guiche(row["name"], row["priority"], new_priority, new_ranges)
            except ValueError:
                continue
            if guiche != row["guiche"]:
                update_participant(db, row["id"], "guiche_reassign", actor, guiche=guiche)
                changed += 1
        db.commit()
    if changed:
        export_csv()
    return changed


def available_guiches(db):
    """List configured desks, including desks assigned explicitly to participants or staff."""
    ranges = configured_ranges()
    desks = {}
    priority = priority_guiche()
    if priority:
        desks[priority] = []
    for item in ranges:
        desk = str(item["guiche"]).strip()
        if desk:
            desks.setdefault(desk, []).append(f'{str(item["from"]).upper()}–{str(item["to"]).upper()}')
    for row in db.execute("SELECT guiche FROM participants UNION SELECT guiche FROM users"):
        desk = row["guiche"].strip()
        if desk:
            desks.setdefault(desk, [])
    def order(entry):
        # Ordem natural (1, 1A, 2, 10…), com o guichê de prioridade por último.
        number = re.match(r"(\d+)(.*)", entry[0])
        natural = (0, int(number.group(1)), number.group(2).casefold()) if number else (1, 0, entry[0].casefold())
        return (entry[0] == priority, natural)
    return [{"id": desk, "ranges": labels, "priority": desk == priority} for desk, labels in sorted(desks.items(), key=order)]


@contextmanager
def connect():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    db = sqlite3.connect(DB, timeout=10, isolation_level=None)
    try:
        os.chmod(DB, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS participants (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                    badge_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, email_key TEXT NOT NULL,
                    cpf TEXT NOT NULL DEFAULT '', affiliation TEXT NOT NULL DEFAULT '',
                    paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
                    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1)), guiche TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN
            ('registered','prechecked','searching','ready','completed')),
          claimed_by TEXT, prechecked_at TEXT, claimed_at TEXT, ready_at TEXT,
          completed_at TEXT, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          UNIQUE(name_key,email_key)
        );
        CREATE INDEX IF NOT EXISTS idx_queue ON participants(status,guiche,prechecked_at);
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('volunteer','attendant','admin')),
          guiche TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, action TEXT NOT NULL,
          actor TEXT NOT NULL, occurred_at TEXT NOT NULL,
          FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        CREATE TABLE IF NOT EXISTS sheet_outbox (
          event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at TEXT,
          attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(event_id) REFERENCES events(id)
        );
        CREATE TABLE IF NOT EXISTS lookup_tokens (
          token_hash TEXT PRIMARY KEY, participant_id TEXT NOT NULL, expires_at REAL NOT NULL,
          FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
        for name, definition in (
            ("badge_name", "TEXT NOT NULL DEFAULT ''"),
            ("paid", "INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1))"),
            ("priority", "INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1))"),
            ("guiche_manual", "INTEGER NOT NULL DEFAULT 0 CHECK(guiche_manual IN (0,1))"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE participants ADD COLUMN {name} {definition}")
        if "guiche_manual" not in columns:
            # Existing desks that differ from the automatic rule were set explicitly on import.
            try:
                ranges, priority_desk = configured_ranges(), priority_guiche()
            except ValueError:
                ranges, priority_desk = [], ""
            for row in db.execute("SELECT id,name,priority,guiche FROM participants").fetchall():
                try:
                    manual = row["guiche"] != auto_guiche(row["name"], row["priority"], priority_desk, ranges)
                except ValueError:
                    manual = True
                if manual:
                    db.execute("UPDATE participants SET guiche_manual=1 WHERE id=?", (row["id"],))
        db.execute("UPDATE participants SET badge_name=name WHERE badge_name='' ")
    try:
        ranges = validate_ranges(configured_ranges())
    except ValueError:
        return
    # Ao iniciar, junta restos de renomeações antigas e leva prioritários ao guichê de prioridade.
    apply_desk_config(ranges, priority_guiche(), ranges, priority_guiche(), "system")


def participant_dict(row, private=False):
    d = {k: row[k] for k in ("id", "name", "affiliation", "guiche", "status",
                                  "claimed_by", "prechecked_at", "claimed_at", "ready_at",
                                  "completed_at", "updated_at", "revision")}
    d["priority"] = bool(row["priority"])
    d["cpf_prefix"] = row["cpf"][:3]
    if private:
        d.update(email=row["email"], cpf=row["cpf"], badge_name=row["badge_name"], paid=bool(row["paid"]))
    return d


def pending_reasons(row):
    reasons = []
    if not row["paid"]:
        reasons.append("payment")
    if not row["affiliation"].strip():
        reasons.append("affiliation")
    return reasons


def record_event(db, row, action, actor):
    event = {"id": str(uuid.uuid4()), "participant_id": row["id"], "action": action,
             "actor": actor, "occurred_at": now()}
    db.execute("INSERT INTO events VALUES (:id,:participant_id,:action,:actor,:occurred_at)", event)
    payload = {"event": event, "participant": participant_dict(row, private=True)}
    db.execute("INSERT INTO sheet_outbox(event_id,payload) VALUES (?,?)",
               (event["id"], json.dumps(payload, ensure_ascii=False)))


FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe(value):
    """Keep spreadsheets from running cell content as a formula."""
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES) else value


def get_participant(db, pid):
    return db.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()


def update_participant(db, pid, action, actor, **fields):
    """Única forma de alterar um participante: grava os campos, atualiza updated_at e revision,
    registra o evento (histórico e fila do Google Sheets) e devolve a linha atualizada."""
    columns = "".join(f"{name}=?," for name in fields)
    db.execute(f"UPDATE participants SET {columns}updated_at=?,revision=revision+1 WHERE id=?",
               (*fields.values(), now(), pid))
    row = get_participant(db, pid)
    record_event(db, row, action, actor)
    return row


STATUS_STEPS = ("registered", "prechecked", "searching", "ready", "completed")
STEP_TIMESTAMPS = {"prechecked": "prechecked_at", "searching": "claimed_at",
                   "ready": "ready_at", "completed": "completed_at"}


def status_fields(row, status, actor):
    """Campos para levar alguém direto a uma etapa: preenche o que ficou para trás (mantendo
    horários já registrados) e limpa as etapas seguintes."""
    target, stamp = STATUS_STEPS.index(status), now()
    fields = {"status": status}
    for step, column in STEP_TIMESTAMPS.items():
        fields[column] = (row[column] or stamp) if STATUS_STEPS.index(step) <= target else None
    if status == "searching":
        fields["claimed_by"] = actor
    elif target > STATUS_STEPS.index("searching"):
        fields["claimed_by"] = row["claimed_by"] or actor
    else:
        fields["claimed_by"] = None
    return fields


def queue_action_fields(action, user, row):
    """Campos de uma ação da fila (busca e guichê), ou None se ela não vale para este usuário agora."""
    role, username, stamp = user["role"], user["username"], now()
    staff = role in ("volunteer", "admin")
    owns_search = row["claimed_by"] == username or role == "admin"
    at_desk = staff or (role == "attendant" and user["guiche"] == row["guiche"])
    clear = not pending_reasons(row)
    if action == "claim" and staff and row["status"] == "prechecked" and clear:
        return {"status": "searching", "claimed_by": username, "claimed_at": stamp}
    if action == "ready" and staff and row["status"] == "searching" and clear and owns_search:
        return {"status": "ready", "ready_at": stamp}
    if action == "release" and staff and row["status"] == "searching" and owns_search:
        return {"status": "prechecked", "claimed_by": None, "claimed_at": None}
    if action == "complete" and at_desk and row["status"] == "ready" and clear:
        return {"status": "completed", "completed_at": stamp}
    if action == "undo_ready" and at_desk and row["status"] == "ready":
        return {"status": "searching" if row["claimed_by"] else "prechecked", "ready_at": None}
    if action == "undo_complete" and at_desk and row["status"] == "completed":
        return {"status": "ready", "completed_at": None}
    return None


def export_csv():
    """The local CSV is a restart-rebuilt mirror; SQLite remains the source of truth."""
    with CSV_LOCK, connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
        fields = ["id", "name", "badge_name", "email", "cpf", "affiliation", "paid", "priority", "guiche", "status",
                  "claimed_by", "prechecked_at", "claimed_at", "ready_at", "completed_at",
                  "updated_at", "revision"]
        temp = CSV.with_suffix(".csv.tmp")
        with temp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: csv_safe(row[key]) for key in fields})
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, CSV)


PARTICIPANT_COLUMNS = ("id", "nome", "nome_cracha", "afiliacao", "email", "cpf", "pago", "prioridade", "guiche")
PARTICIPANT_EXPORT_COLUMNS = PARTICIPANT_COLUMNS + ("situacao", "responsavel", "pre_checkin_em",
                                                      "busca_iniciada_em", "pronto_em", "retirado_em",
                                                      "atualizado_em", "revisao")
PARTICIPANT_TEMPLATE_COLUMNS = ("nome", "nome_cracha", "afiliacao", "email", "cpf", "pago", "prioridade")
EVENT_LOG_COLUMNS = ("evento_id", "participante_id", "nome", "nome_cracha", "email", "cpf", "guiche",
                     "acao", "responsavel", "data_hora")


def participants_download():
    """Export participant data, including workflow fields accepted on reimport."""
    with connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        values = {"id": row["id"], "nome": row["name"], "nome_cracha": row["badge_name"],
                  "afiliacao": row["affiliation"], "email": row["email"], "cpf": row["cpf"],
                  "pago": row["paid"], "prioridade": row["priority"], "guiche": row["guiche"],
                  "situacao": row["status"], "responsavel": row["claimed_by"],
                  "pre_checkin_em": row["prechecked_at"], "busca_iniciada_em": row["claimed_at"],
                  "pronto_em": row["ready_at"], "retirado_em": row["completed_at"],
                  "atualizado_em": row["updated_at"], "revisao": row["revision"]}
        writer.writerow({key: csv_safe(value) for key, value in values.items()})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def participants_template_download():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_TEMPLATE_COLUMNS)
    writer.writeheader()
    writer.writerow({"nome": "Maria da Silva", "nome_cracha": "Maria", "afiliacao": "Universidade Exemplo",
                     "email": "maria@example.org", "cpf": "12345678901", "pago": "1", "prioridade": "0"})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def event_logs_download():
    with connect() as db:
        rows = db.execute("""
            SELECT e.id AS evento_id, e.participant_id AS participante_id, p.name AS nome,
                   p.badge_name AS nome_cracha, p.email, p.cpf, p.guiche,
                   e.action AS acao, e.actor AS responsavel, e.occurred_at AS data_hora
            FROM events e
            LEFT JOIN participants p ON p.id=e.participant_id
            ORDER BY e.occurred_at DESC, e.id DESC
        """).fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EVENT_LOG_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: csv_safe(row[key] or "") for key in EVENT_LOG_COLUMNS})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def sign(data):
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    return raw.decode() + "." + sig


def unsign(token):
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return data if data.get("exp", 0) >= time.time() else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def allowed(ip, limit=12, window=60):
    with RATE_LOCK:
        q = RATE[ip]
        t = time.time()
        while q and q[0] < t - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


class App(BaseHTTPRequestHandler):
    server_version = "CheckIn/1"

    def log_message(self, fmt, *args):
        # Avoid logging lookup data or query strings.
        print(f"{self.address_string()} - {fmt % args}")

    def client_ip(self):
        peer = self.client_address[0]
        if TRUST_PROXY and peer in ("127.0.0.1", "::1"):
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return peer

    def respond(self, status, data, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, filename, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, max_bytes=16384):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Envie dados em JSON.")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > max_bytes:
            raise ValueError("Dados inválidos ou muito grandes.")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Dados inválidos.")
        return data

    def session(self):
        cookies = self.headers.get("Cookie", "").split(";")
        token = next((x.strip()[8:] for x in cookies if x.strip().startswith("session=")), "")
        data = unsign(token)
        if not data or data.get("kind") != "session":
            return None
        with connect() as db:
            user = db.execute("SELECT username,role,guiche FROM users WHERE username=?", (data.get("user"),)).fetchone()
        return dict(user) if user else None

    def require(self, roles):
        user = self.session()
        if not user or user["role"] not in roles:
            self.respond(HTTPStatus.UNAUTHORIZED, {"error": "Entre com uma conta autorizada."})
            return None
        return user

    def check_csrf(self, user):
        expected = hmac.new(SECRET, (user["username"] + ":csrf").encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), expected)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.api_get(path)
        if path == "/theme.css":
            body = theme_css(event_config()).encode("utf-8")
            mime = "text/css"
        elif path.startswith("/assets/"):
            asset = path.removeprefix("/assets/")
            config = event_config()
            allowed = {config["logo"], *config["fonts"].values()}
            if config.get("decoration"):
                allowed.add(config["decoration"])
            if asset not in allowed:
                return self.send_error(404)
            body = (STATIC / "assets" / asset).read_bytes()
            mime = ("image/png" if asset.endswith(".png") else
                    "image/jpeg" if asset.endswith((".jpg", ".jpeg")) else
                    "image/webp" if asset.endswith(".webp") else
                    "image/svg+xml" if asset.endswith(".svg") else "font/woff2")
        else:
            routes = {"/": "index.html", "/busca": "busca.html", "/fila": "fila.html",
                      "/painel": "painel.html",
                      "/painel/resumo": "resumo.html", "/login": "login.html",
                      "/app.css": "app.css", "/app.js": "app.js"}
            filename = routes.get(path)
            if not filename:
                return self.send_error(404)
            mime = ("text/css" if filename.endswith(".css") else
                    "text/javascript" if filename.endswith(".js") else "text/html")
            body = (STATIC / filename).read_bytes()
            if mime == "text/html":
                config = event_config()
                body = (body.decode("utf-8")
                        .replace("{{EVENT_NAME}}", html.escape(config["name"]))
                        .replace("{{EVENT_SHORT_NAME}}", html.escape(config["short_name"]))
                        .replace("{{EVENT_LOGO}}", "/assets/" + config["logo"])).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path):
        if path == "/api/event":
            config = event_config()
            return self.respond(200, {"name": config["name"], "short_name": config["short_name"],
                                      "logo": "/assets/" + config["logo"],
                                      "registration_hints": {
                                          "en": config.get("registration_hint_en"),
                                          "pt-BR": config.get("registration_hint_pt")
                                      }})
        if path == "/api/me":
            user = self.session()
            if not user:
                return self.respond(200, {"user": None})
            csrf = hmac.new(SECRET, (user["username"] + ":csrf").encode(), hashlib.sha256).hexdigest()
            return self.respond(200, {"user": user, "csrf": csrf})
        if path == "/api/guiches":
            user = self.require(("volunteer", "attendant", "admin"))
            if not user:
                return
            with connect() as db:
                desks = available_guiches(db)
            return self.respond(200, {"guiches": desks})
        if path == "/api/queue":
            user = self.require(("volunteer", "attendant", "admin"))
            if not user:
                return
            params = parse_qs(urlparse(self.path).query)
            requested = params.get("guiche", [""])[0].strip()
            attendant_view = params.get("view", [""])[0] == "attendant"
            guiche = "" if requested == "all" else requested
            if user["role"] == "attendant" and not requested:
                guiche = user["guiche"]
            with connect() as db:
                statuses = "('ready','completed')" if attendant_view else "('prechecked','searching','ready')"
                query = f"SELECT * FROM participants WHERE status IN {statuses} AND paid=1 AND TRIM(affiliation)<>''"
                args = []
                if guiche:
                    query += " AND guiche=?"
                    args.append(guiche)
                if attendant_view:
                    query += (" ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,"
                              " CASE WHEN status='ready' THEN ready_at END ASC,"
                              " CASE WHEN status='completed' THEN completed_at END DESC, name_key ASC")
                else:
                    query += " ORDER BY priority DESC, prechecked_at ASC, name_key ASC"
                rows = db.execute(query, args).fetchall()
            return self.respond(200, {"items": [participant_dict(r) for r in rows], "guiche": guiche})
        if path == "/api/dashboard/summary":
            with connect() as db:
                total, arrived, completed = db.execute("""
                    SELECT COUNT(*),
                           COALESCE(SUM(status <> 'registered'), 0),
                           COALESCE(SUM(status = 'completed'), 0)
                    FROM participants
                """).fetchone()
            return self.respond(200, {"total": total, "arrived": arrived, "completed": completed})
        if path == "/api/dashboard":
            user = self.require(("admin",))
            if not user:
                return
            with connect() as db:
                rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
                pending = db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0]
                desks = available_guiches(db)
            for desk in desks:
                desk["total"] = sum(r["guiche"] == desk["id"] for r in rows)
            counts = {s: sum(r["status"] == s for r in rows) for s in
                      ("registered", "prechecked", "searching", "ready", "completed")}
            return self.respond(200, {"total": len(rows), "counts": counts,
                                      "guidance_pending": sum(r["status"] not in ("registered", "completed")
                                                              and bool(pending_reasons(r)) for r in rows),
                                      "sheet_pending": pending, "sheet_configured": bool(SHEET_URL and SHEET_SECRET),
                                      "desks": desks,
                                      # Pagamento ou afiliação faltando em quem ainda não foi credenciado.
                                      "registration_pending": sum(r["status"] != "completed" and bool(pending_reasons(r))
                                                                  for r in rows),
                                      "items": [participant_dict(r, private=True) for r in rows]})
        if path == "/api/participants/export":
            if not self.require(("admin",)):
                return
            return self.send_csv("dados-participantes.csv", participants_download())
        if path == "/api/events/export":
            if not self.require(("admin",)):
                return
            return self.send_csv("logs-movimentacoes.csv", event_logs_download())
        if path == "/api/google-sheets/script":
            if not self.require(("admin",)):
                return
            try:
                script = GOOGLE_SHEETS_SCRIPT.read_text(encoding="utf-8")
            except OSError:
                return self.respond(500, {"error": "Não foi possível ler o Apps Script do backup."})
            return self.respond(200, {"script": script})
        if path == "/api/participants/template":
            if not self.require(("admin",)):
                return
            return self.send_csv("participantes-exemplo.csv", participants_template_download())
        if path == "/api/guiches/config":
            if not self.require(("admin",)):
                return
            try:
                return self.respond(200, {"ranges": configured_ranges(), "priority_guiche": priority_guiche()})
            except ValueError as exc:
                return self.respond(500, {"error": str(exc)})
        self.respond(404, {"error": "Rota não encontrada."})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.body(2_000_000 if path == "/api/participants/import" else 16384)
        except (ValueError, json.JSONDecodeError) as exc:
            return self.respond(400, {"error": str(exc)})
        if path == "/api/lookup":
            if not allowed(self.client_ip()):
                return self.respond(429, {"error": "Muitas tentativas. Aguarde um minuto."})
            cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
            name_key = normalize(str(data.get("name", "")))
            email_key = str(data.get("email", "")).strip().casefold()
            if cpf and not re.fullmatch(r"\d{11}", cpf):
                return self.respond(400, {"error": "Informe um CPF válido com 11 dígitos."})
            if not cpf and (len(name_key) < 3 or "@" not in email_key):
                return self.respond(400, {"error": "Informe o CPF ou o nome completo e o e-mail da inscrição."})
            with connect() as db:
                if cpf:
                    matches = db.execute("SELECT * FROM participants WHERE cpf=? LIMIT 2", (cpf,)).fetchall()
                else:
                    matches = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=? LIMIT 2",
                                         (name_key, email_key)).fetchall()
            if len(matches) > 1:
                return self.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
            row = matches[0] if matches else None
            if not row:
                return self.respond(404, {"error": "Inscrição indisponível. Confira os dados ou procure atendimento."})
            token = secrets.token_urlsafe(32)
            with connect() as db:
                db.execute("DELETE FROM lookup_tokens WHERE expires_at<?", (time.time(),))
                db.execute("INSERT INTO lookup_tokens(token_hash,participant_id,expires_at) VALUES (?,?,?)",
                           (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + 600))
            # The public page never learns why a registration is pending.
            return self.respond(200, {"name": mask_public_text(row["name"]),
                                      "affiliation": mask_public_text(row["affiliation"]), "token": token})
        if path == "/api/precheck":
            token = data.get("token", "")
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                return self.respond(400, {"error": "Consulta expirada. Tente novamente."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                token_row = db.execute("SELECT participant_id FROM lookup_tokens WHERE token_hash=? AND expires_at>=?",
                                       (token_hash, time.time())).fetchone()
                if not token_row:
                    return self.respond(400, {"error": "Consulta expirada. Tente novamente."})
                db.execute("DELETE FROM lookup_tokens WHERE token_hash=?", (token_hash,))
                row = get_participant(db, token_row["participant_id"])
                if not row:
                    return self.respond(404, {"error": "Inscrição não encontrada."})
                needs_guidance = bool(pending_reasons(row))
                if row["status"] == "registered":
                    row = update_participant(db, row["id"], "precheck_pending" if needs_guidance else "precheck",
                                             "participant", status="prechecked", prechecked_at=now())
                    db.commit()
                    export_csv()
            result = {"ok": True, "name": mask_public_text(row["name"]), "needs_guidance": needs_guidance}
            if not needs_guidance:
                # Pending registrations are sent to a volunteer instead of a desk.
                result.update(guiche=row["guiche"], guiche_ranges=desk_ranges(row["guiche"]),
                              guiche_priority=row["guiche"] == priority_guiche())
            return self.respond(200, result)
        if path == "/api/login":
            if not allowed("login:" + self.client_ip(), 8, 300):
                return self.respond(429, {"error": "Muitas tentativas. Aguarde alguns minutos."})
            username = str(data.get("username", "")).strip().casefold()
            password = str(data.get("password", ""))
            with connect() as db:
                user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user or not hmac.compare_digest(password_hash(password, user["salt"]), user["password_hash"]):
                return self.respond(401, {"error": "Usuário ou senha incorretos."})
            token = sign({"kind": "session", "user": username, "exp": time.time() + 12*3600})
            secure = "; Secure" if PUBLIC_URL.startswith("https://") else ""
            return self.respond(200, {"role": user["role"]}, {"Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}"})
        user = self.require(("volunteer", "attendant", "admin"))
        if not user:
            return
        if not self.check_csrf(user):
            return self.respond(403, {"error": "Sessão inválida. Recarregue a página."})
        if path == "/api/logout":
            return self.respond(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if path == "/api/participants/import":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode importar participantes."})
            filename, content = data.get("filename"), data.get("content")
            if not isinstance(filename, str) or not isinstance(content, str) or not content.strip():
                return self.respond(400, {"error": "Selecione um arquivo CSV ou JSON válido."})
            suffix = Path(filename).suffix.lower()
            if suffix not in (".csv", ".json"):
                return self.respond(400, {"error": "Use um arquivo CSV ou JSON."})
            try:
                read, changed = import_text(content, suffix, actor=user["username"])
            except (ValueError, json.JSONDecodeError, csv.Error, sqlite3.IntegrityError) as exc:
                return self.respond(400, {"error": str(exc)})
            return self.respond(200, {"read": read, "changed": changed})
        if path == "/api/participants/payment":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar o pagamento."})
            pid, paid = str(data.get("id", "")), str(data.get("paid", "")).strip()
            if paid not in ("0", "1"):
                return self.respond(400, {"error": "Pagamento inválido."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if row["paid"] != int(paid):
                    row = update_participant(db, pid, "payment_paid" if int(paid) else "payment_unpaid",
                                             user["username"], paid=int(paid))
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/participants/priority":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a prioridade."})
            pid, priority = str(data.get("id", "")), str(data.get("priority", "")).strip()
            if priority not in ("0", "1"):
                return self.respond(400, {"error": "Prioridade inválida."})
            priority = int(priority)
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                desk, guiche = priority_guiche(), row["guiche"]
                try:
                    if priority and desk:
                        guiche = desk
                    elif not priority and guiche == desk:
                        guiche = guiche_for(row["name"])
                except ValueError as exc:
                    return self.respond(400, {"error": str(exc)})
                if guiche != row["guiche"] and row["status"] in ("searching", "ready", "completed"):
                    return self.respond(409, {"error": "Não é possível mudar o guichê: a busca ou a retirada do kit já começou."})
                if row["priority"] != priority or row["guiche"] != guiche:
                    manual = 0 if priority and desk else row["guiche_manual"] if guiche == row["guiche"] else 0
                    row = update_participant(db, pid, "priority_on" if priority else "priority_off", user["username"],
                                             priority=priority, guiche=guiche, guiche_manual=manual)
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/participants/affiliation":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a afiliação."})
            pid, affiliation = str(data.get("id", "")), " ".join(str(data.get("affiliation", "")).split())
            if not affiliation or len(affiliation) > 200:
                return self.respond(400, {"error": "Informe a afiliação com até 200 caracteres."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if row["affiliation"] != affiliation:
                    row = update_participant(db, pid, "affiliation_update", user["username"], affiliation=affiliation)
                    db.commit()
                    export_csv()
            return self.respond(200, {"item": participant_dict(row, private=True)})
        if path == "/api/guiches/config":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode configurar guichês."})
            try:
                ranges, priority, changed = save_ranges(data.get("ranges"), data.get("priority_guiche"), user["username"])
            except (ValueError, OSError) as exc:
                return self.respond(400, {"error": str(exc)})
            return self.respond(200, {"ranges": ranges, "priority_guiche": priority, "updated": changed})
        if path == "/api/participants/status":
            if user["role"] != "admin":
                return self.respond(403, {"error": "Somente a coordenação pode alterar a situação."})
            pid, status = str(data.get("id", "")), str(data.get("status", ""))
            if status not in STATUS_STEPS:
                return self.respond(400, {"error": "Situação inválida."})
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                if status in ("searching", "ready", "completed") and pending_reasons(row):
                    return self.respond(409, {"error": "Há pagamento ou afiliação pendente. Resolva a pendência antes de avançar a situação."})
                row = update_participant(db, pid, "status_" + status, user["username"],
                                         **status_fields(row, status, user["username"]))
                db.commit()
            export_csv()
            return self.respond(200, {"item": participant_dict(row)})
        if path.startswith("/api/action/"):
            action = path.removeprefix("/api/action/")
            if action not in ("claim", "ready", "release", "complete", "undo_ready", "undo_complete"):
                return self.respond(404, {"error": "Ação desconhecida."})
            pid = str(data.get("id", ""))
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = get_participant(db, pid)
                if not row:
                    return self.respond(404, {"error": "Participante não encontrado."})
                fields = queue_action_fields(action, user, row)
                if fields is None:
                    return self.respond(409, {"error": "Esta ação não é permitida no estado atual. Atualize a fila."})
                row = update_participant(db, pid, action, user["username"], **fields)
                db.commit()
            export_csv()
            return self.respond(200, {"item": participant_dict(row)})
        self.respond(404, {"error": "Rota não encontrada."})


def sync_sheets_once():
    if not SHEET_URL or not SHEET_SECRET:
        return 0
    with connect() as db:
        rows = db.execute("SELECT event_id,payload FROM sheet_outbox WHERE delivered_at IS NULL ORDER BY rowid LIMIT 40").fetchall()
    if not rows:
        return 0
    payload = {"secret": SHEET_SECRET, "records": [json.loads(r["payload"]) for r in rows]}
    request = urllib.request.Request(SHEET_URL, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise ValueError(result.get("error", "Google Sheets recusou a sincronização"))
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET delivered_at=?,attempts=attempts+1,last_error='' WHERE event_id=?",
                           [(now(), r["event_id"]) for r in rows])
        return len(rows)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET attempts=attempts+1,last_error=? WHERE event_id=?",
                           [(str(exc)[:300], r["event_id"]) for r in rows])
        return 0


def sync_worker():
    while True:
        sync_sheets_once()
        time.sleep(15)


def import_file(path):
    path = Path(path)
    result = import_text(path.read_text(encoding="utf-8-sig"), path.suffix.lower())
    print(f"{result[0]} registros lidos; {result[1]} criados ou atualizados.")
    return result


def import_text(content, suffix, actor="system"):
    if suffix == ".json":
        records = json.loads(content.lstrip("\ufeff"))
        if isinstance(records, dict):
            records = records.get("participants", [])
    elif suffix == ".csv":
        content = content.lstrip("\ufeff")
        required = {"nome", "email"}
        readers = [csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter)
                   for delimiter in (",", ";", "\t")]
        reader = next((candidate for candidate in readers
                       if required.issubset({field.strip() for field in candidate.fieldnames or []})), None)
        if reader is None:
            raise ValueError("Cabeçalho CSV inválido: use nome e email como colunas. "
                             "Separe as colunas por vírgula, ponto e vírgula ou tabulação.")
        fields = [field.strip() for field in reader.fieldnames]
        if len(fields) != len(set(fields)):
            raise ValueError("Cabeçalho CSV contém colunas duplicadas.")
        reader.fieldnames = fields
        records = [item for item in reader if any(str(value or "").strip() for value in item.values())]
    else:
        raise ValueError("Use um arquivo CSV ou JSON.")
    if not isinstance(records, list) or not records:
        raise ValueError("Arquivo não contém participantes. Inclua pelo menos uma linha de dados além do cabeçalho.")
    prepared = []
    seen, seen_ids = set(), set()
    priority_desk = priority_guiche()
    try:
        ranges = configured_ranges()
    except ValueError:
        ranges = []
    for n, item in enumerate(records, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Linha {n}: registro inválido.")
        def value(key):
            raw = item.get(key, "")
            if raw is None:
                return ""
            raw = str(raw).strip()
            return raw[1:] if raw.startswith("'") and raw[1:].lstrip().startswith(FORMULA_PREFIXES) else raw

        pid = value("id")
        if pid:
            try:
                pid = str(uuid.UUID(pid))
            except ValueError as exc:
                raise ValueError(f"Linha {n}: id inválido.") from exc
            if pid in seen_ids:
                raise ValueError(f"Linha {n}: id duplicado.")
            seen_ids.add(pid)
        name, badge_name = value("nome"), value("nome_cracha")
        affiliation, email, cpf = value("afiliacao"), value("email"), value("cpf")
        if not name or not email:
            raise ValueError(f"Linha {n}: nome e email são obrigatórios.")
        if not badge_name:
            name_parts = name.split()
            badge_name = name_parts[0] if len(name_parts) == 1 else f"{name_parts[0]} {name_parts[-1]}"
        if cpf and not re.fullmatch(r"(?:\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})", cpf):
            raise ValueError(f"Linha {n}: cpf deve ter 11 dígitos ou usar o formato xxx.xxx.xxx-xx.")
        cpf = re.sub(r"\D", "", cpf)
        paid_raw, priority_raw = value("pago"), value("prioridade")
        if paid_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: pago deve ser 0 ou 1.")
        if priority_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: prioridade deve ser 0 ou 1.")
        paid, priority = int(paid_raw or 0), int(priority_raw or 0)
        explicit = value("guiche")
        if priority and priority_desk:
            # Prioridade manda: quem credencia define a prioridade, e ela vale mais que a coluna guiche.
            guiche, manual = priority_desk, 0
        else:
            try:
                automatic = guiche_for(name, ranges)
            except ValueError:
                if not explicit:
                    raise
                automatic = ""
            # An exported file repeats the computed desk; only a different value is a manual choice.
            guiche, manual = explicit or automatic, int(bool(explicit) and explicit != automatic)
        key = (normalize(name), email.casefold())
        if len(key[0]) < 3 or "@" not in email or not guiche:
            raise ValueError(f"Linha {n}: nome e email devem ser válidos; guiche deve ser informado ou calculável.")
        if key in seen:
            raise ValueError(f"Linha {n}: nome e e-mail duplicados.")
        seen.add(key)
        prepared.append((pid, name, badge_name, key[0], email, key[1], cpf, affiliation, paid, priority, guiche, manual))
    count = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for pid, name, badge_name, name_key, email, email_key, cpf, affiliation, paid, priority, guiche, manual in prepared:
            old_by_id = get_participant(db, pid) if pid else None
            old_by_key = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=?",
                                    (name_key, email_key)).fetchone()
            if old_by_id and old_by_key and old_by_id["id"] != old_by_key["id"]:
                raise ValueError(f"{name}: id e nome/e-mail identificam pessoas diferentes.")
            old = old_by_id or old_by_key
            if old:
                if any(old[k] != v for k, v in (("name", name), ("name_key", name_key),
                                                  ("badge_name", badge_name), ("email", email), ("email_key", email_key),
                                                  ("cpf", cpf), ("affiliation", affiliation), ("paid", paid),
                                                  ("priority", priority), ("guiche", guiche))):
                    if old["guiche"] != guiche and old["status"] in ("searching", "ready", "completed"):
                        raise ValueError(f"Não é possível mudar o guichê de {name}: busca ou retirada já iniciada.")
                    update_participant(db, old["id"], "import_update", actor, name=name, name_key=name_key,
                                       badge_name=badge_name, email=email, email_key=email_key, cpf=cpf,
                                       affiliation=affiliation, paid=paid, priority=priority, guiche=guiche)
                    count += 1
                if old["guiche_manual"] != manual:
                    db.execute("UPDATE participants SET guiche_manual=? WHERE id=?", (manual, old["id"]))
            else:
                pid = pid or str(uuid.uuid4())
                db.execute("INSERT INTO participants(id,name,badge_name,name_key,email,email_key,cpf,affiliation,paid,priority,guiche,guiche_manual,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, name, badge_name, name_key, email, email_key, cpf, affiliation, paid, priority, guiche, manual, now()))
                record_event(db, get_participant(db, pid), "import", actor)
                count += 1
        db.commit()
    export_csv()
    return len(prepared), count


def backup_file(path):
    target = Path(path).expanduser().resolve()
    if target == DB.resolve():
        raise ValueError("Escolha um arquivo de backup diferente do banco ativo.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("O backup já existe. Escolha outro nome para preservar o anterior.")
    with connect() as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    os.chmod(target, 0o600)
    print(f"Backup consistente salvo em {target}")


def main():
    parser = argparse.ArgumentParser(description="Sistema de credenciamento")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    imp = sub.add_parser("import")
    imp.add_argument("file")
    user = sub.add_parser("user")
    user.add_argument("username")
    user.add_argument("role", choices=("volunteer", "attendant", "admin"))
    user.add_argument("--guiche", default="")
    sub.add_parser("sync")
    backup = sub.add_parser("backup")
    backup.add_argument("file")
    args = parser.parse_args()
    init_db()
    if args.command == "import":
        import_file(args.file)
    elif args.command == "user":
        import getpass
        if args.role == "attendant" and not args.guiche:
            parser.error("Atendente precisa de --guiche.")
        password = getpass.getpass("Senha (mínimo 10 caracteres): ")
        if len(password) < 10:
            parser.error("Senha curta demais.")
        salt = secrets.token_hex(16)
        with connect() as db:
            db.execute("INSERT INTO users(username,salt,password_hash,role,guiche) VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,role=excluded.role,guiche=excluded.guiche",
                       (args.username.casefold(), salt, password_hash(password, salt), args.role, args.guiche))
        print("Usuário salvo.")
    elif args.command == "sync":
        print(f"{sync_sheets_once()} eventos sincronizados.")
    elif args.command == "backup":
        backup_file(args.file)
    elif args.command == "serve":
        if len(SECRET) < 32:
            parser.error("Defina CHECKIN_SESSION_SECRET com pelo menos 32 caracteres.")
        try:
            event_config()
        except ValueError as exc:
            parser.error(str(exc))
        export_csv()
        threading.Thread(target=sync_worker, daemon=True).start()
        print(f"http://{args.host}:{args.port}")
        ThreadingHTTPServer((args.host, args.port), App).serve_forever()


if __name__ == "__main__":
    main()
