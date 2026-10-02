"""Conexão com o banco, backup e sincronização com o Google Sheets."""
import io
import json
import sqlite3
import unittest
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from credenciamento import csv_io, lookup, settings, sheets
from credenciamento import db as database
from credenciamento.bootstrap import init_db
from credenciamento.participants import set_public_checkin
from support import CredenciamentoTestCase


class StorageTest(CredenciamentoTestCase):
    def test_connect_closes_after_commit_and_rollback(self):
        with database.connect() as db:
            db.execute("INSERT INTO users VALUES (?,?,?,?,?)", ("temporary", "salt", "hash", "volunteer", ""))
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        try:
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO users VALUES (?,?,?,?,?)", ("rolled-back", "salt", "hash", "volunteer", ""))
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE username='rolled-back'").fetchone()[0], 0)

    def test_sheet_outbox_is_acknowledged_only_after_success(self):
        settings.SHEET_URL = "https://example.org/script"
        settings.SHEET_SECRET = "test-secret"
        captured = []

        def fake_open(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(b'{"ok":true}')

        with patch.object(urllib.request, "urlopen", fake_open):
            self.assertEqual(sheets.sync_sheets_once(), 2)
        self.assertEqual(len(captured[0]["records"]), 2)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0], 0)

    def test_backup_is_consistent_and_does_not_overwrite(self):
        import sqlite3
        target = Path(self.temp.name) / "copy.sqlite3"
        database.backup_file(target)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            database.backup_file(target)

    def test_cpf_and_email_are_never_stored_in_plain_text(self):
        csv_io.export_csv()
        with database.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
        self.assertFalse({"email", "cpf"} & columns)
        stored = settings.DB.read_bytes() + settings.CSV.read_bytes()
        for secret in (b"ana@example.org", b"12345678901", b"98765432100", b"987.654.321-00"):
            self.assertNotIn(secret, stored)
        with database.connect() as db:
            row = db.execute("SELECT cpf_key, cpf_prefix FROM participants WHERE name_key='ana silva'").fetchone()
        self.assertEqual(tuple(row), (lookup.cpf_key("123.456.789-01"), "123"))
        # Outra chave de busca gera outros valores: sem ela, as chaves não servem para nada.
        with patch.object(settings, "LOOKUP_SECRET", b"another-lookup-secret-longer-than-32-chars"):
            self.assertNotEqual(lookup.cpf_key("12345678901"), row["cpf_key"])
        with patch.object(settings, "LOOKUP_SECRET", b"short"), self.assertRaises(ValueError):
            lookup.cpf_key("12345678901")

    def test_old_database_with_plain_text_is_migrated_and_scrubbed(self):
        settings.DB.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(settings.DB) + suffix).unlink(missing_ok=True)
        with closing(sqlite3.connect(settings.DB)) as db, db:
            db.executescript("""
                CREATE TABLE participants (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                  badge_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL, email_key TEXT NOT NULL,
                  cpf TEXT NOT NULL DEFAULT '', affiliation TEXT NOT NULL DEFAULT '',
                  paid INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0, guiche TEXT NOT NULL,
                  guiche_manual INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'registered',
                  claimed_by TEXT, prechecked_at TEXT, claimed_at TEXT, ready_at TEXT, completed_at TEXT,
                  updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, UNIQUE(name_key,email_key));
                CREATE TABLE events (id TEXT PRIMARY KEY, participant_id TEXT NOT NULL, action TEXT NOT NULL,
                  actor TEXT NOT NULL, occurred_at TEXT NOT NULL);
                CREATE TABLE sheet_outbox (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at TEXT,
                  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
                INSERT INTO participants(id,name,name_key,badge_name,email,email_key,cpf,guiche,updated_at)
                  VALUES ('p1','Carla Souza','carla souza','Carla','Carla@Example.org','carla@example.org',
                          '11122233344','1','2026-01-01T00:00:00+00:00');
                INSERT INTO events VALUES ('e1','p1','import','system','2026-01-01T00:00:00+00:00');
                INSERT INTO sheet_outbox(event_id,payload) VALUES ('e1',
                  '{"event":{"id":"e1"},"participant":{"id":"p1","email":"Carla@Example.org","cpf":"11122233344"}}');
            """)
        settings.CSV.write_text("id,email,cpf\np1,Carla@Example.org,11122233344\n", encoding="utf-8")
        init_db()
        self.assertNotIn("11122233344", settings.CSV.read_text(encoding="utf-8"))
        with database.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(participants)")}
            row = db.execute("SELECT * FROM participants WHERE id='p1'").fetchone()
            payload = json.loads(db.execute("SELECT payload FROM sheet_outbox").fetchone()[0])
        self.assertFalse({"email", "cpf"} & columns)
        self.assertEqual((row["email_key"], row["cpf_key"], row["cpf_prefix"]),
                         (lookup.email_key("carla@example.org"), lookup.cpf_key("11122233344"), "111"))
        self.assertEqual(payload["participant"], {"id": "p1"})
        wal = Path(str(settings.DB) + "-wal")
        stored = settings.DB.read_bytes() + (wal.read_bytes() if wal.exists() else b"")
        self.assertNotIn(b"11122233344", stored)
        self.assertNotIn(b"carla@example.org", stored.lower())
        # Banco novo começa com o pré-check-in fechado.
        self.assertEqual(self.request("/api/checkin", {"cpf": "111.222.333-44"})[0], 403)
        with database.connect() as db:
            set_public_checkin(db, True, "admin")
        self.assertEqual(self.request("/api/checkin", {"cpf": "111.222.333-44"})[0], 200)


if __name__ == "__main__":
    unittest.main()
