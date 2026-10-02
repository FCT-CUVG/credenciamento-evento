"""Banco SQLite: conexão, esquema e o ponto único de leitura/alteração de participantes."""
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from . import settings
from .common import now
from .lookup import cpf_digits, cpf_key, email_key


@contextmanager
def connect():
    settings.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(settings.DATA, 0o700)
    db = sqlite3.connect(settings.DB, timeout=10, isolation_level=None)
    try:
        os.chmod(settings.DB, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            yield db
    finally:
        db.close()


def create_schema(db):
    """Cria as tabelas e aplica migrações de coluna; devolve as colunas adicionadas agora."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS participants (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                badge_name TEXT NOT NULL DEFAULT '', email_key TEXT NOT NULL,
                cpf_key TEXT NOT NULL DEFAULT '', cpf_prefix TEXT NOT NULL DEFAULT '',
                affiliation TEXT NOT NULL DEFAULT '',
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
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
    CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    -- A busca pública passou a registrar a chegada num passo só, sem token de confirmação.
    DROP TABLE IF EXISTS lookup_tokens;
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
    added = set()
    for name, definition in (
        ("badge_name", "TEXT NOT NULL DEFAULT ''"),
        ("paid", "INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1))"),
        ("priority", "INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1))"),
        ("guiche_manual", "INTEGER NOT NULL DEFAULT 0 CHECK(guiche_manual IN (0,1))"),
        ("cpf_key", "TEXT NOT NULL DEFAULT ''"),
        ("cpf_prefix", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE participants ADD COLUMN {name} {definition}")
            added.add(name)
    db.execute("UPDATE participants SET badge_name=name WHERE badge_name='' ")
    if "cpf" in columns:
        remove_plaintext_identifiers(db)
        added.add("lookup_keys")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cpf_key ON participants(cpf_key)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_email_key ON participants(email_key)")
    return added


def remove_plaintext_identifiers(db):
    """Bancos antigos guardavam CPF e e-mail em texto: troca pelas chaves HMAC, tira os dois do
    histórico pendente do Google Sheets e apaga as colunas (VACUUM reescreve o arquivo sem elas)."""
    db.execute("BEGIN IMMEDIATE")
    for row in db.execute("SELECT id, email, cpf FROM participants").fetchall():
        digits = cpf_digits(row["cpf"])
        db.execute("UPDATE participants SET email_key=?, cpf_key=?, cpf_prefix=? WHERE id=?",
                   (email_key(row["email"]), cpf_key(digits), digits[:3], row["id"]))
    for row in db.execute("SELECT event_id, payload FROM sheet_outbox").fetchall():
        payload = json.loads(row["payload"])
        for name in ("email", "cpf"):
            payload.get("participant", {}).pop(name, None)
        db.execute("UPDATE sheet_outbox SET payload=? WHERE event_id=?",
                   (json.dumps(payload, ensure_ascii=False), row["event_id"]))
    db.execute("COMMIT")
    db.execute("ALTER TABLE participants DROP COLUMN email")
    db.execute("ALTER TABLE participants DROP COLUMN cpf")
    db.execute("VACUUM")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def participant_dict(row, private=False):
    d = {k: row[k] for k in ("id", "name", "affiliation", "guiche", "status",
                                  "claimed_by", "prechecked_at", "claimed_at", "ready_at",
                                  "completed_at", "updated_at", "revision")}
    d["priority"] = bool(row["priority"])
    d["cpf_prefix"] = row["cpf_prefix"]
    if private:
        d.update(badge_name=row["badge_name"], paid=bool(row["paid"]), has_cpf=bool(row["cpf_key"]))
    return d


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(db, key, value):
    db.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))


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


def backup_file(path):
    target = Path(path).expanduser().resolve()
    if target == settings.DB.resolve():
        raise ValueError("Escolha um arquivo de backup diferente do banco ativo.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("O backup já existe. Escolha outro nome para preservar o anterior.")
    with connect() as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    os.chmod(target, 0o600)
    print(f"Backup consistente salvo em {target}")
