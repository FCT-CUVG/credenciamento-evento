"""Conexão com o banco, backup e sincronização com o Google Sheets."""
import io
import json
import sqlite3
import unittest
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from credenciamento import settings, sheets
from credenciamento import db as database
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


if __name__ == "__main__":
    unittest.main()
