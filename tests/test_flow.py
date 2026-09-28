import json
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from email.parser import Parser
from pathlib import Path
from unittest.mock import patch

import app


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DATA = Path(self.temp.name) / "data"
        app.DB = app.DATA / "credenciamento.sqlite3"
        app.CSV = app.DATA / "participantes.csv"
        app.SECRET = b"a-test-secret-that-is-longer-than-32-characters"
        app.SHEET_URL = ""
        app.SHEET_SECRET = ""
        app.RATE.clear()
        app.init_db()
        input_file = Path(self.temp.name) / "people.json"
        input_file.write_text(json.dumps([
            {"name": "Ána Silva", "email": "ana@example.org", "affiliation": "Instituto A"},
            {"name": "Bruno Lima", "email": "bruno@example.org"},
        ]), encoding="utf-8")
        app.import_file(input_file)
        with app.connect() as db:
            for username, role, guiche in (("vol1", "volunteer", ""), ("vol2", "volunteer", ""),
                                            ("att1", "attendant", "1"), ("admin", "admin", "")):
                salt = "00" * 16
                db.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                           (username, salt, app.password_hash("strong-password", salt), role, guiche))
    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, data=None, cookie="", csrf="", json_response=True):
        headers = Message()
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if data is not None:
            headers["Content-Type"] = "application/json"
        body = b"" if data is None else json.dumps(data).encode()
        headers["Content-Length"] = str(len(body))
        handler = app.App.__new__(app.App)
        handler.path = path
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 0)
        handler.requestline = ("POST " if data is not None else "GET ") + path + " HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        if data is None:
            handler.do_GET()
        else:
            handler.do_POST()
        raw = handler.wfile.getvalue()
        head, payload = raw.split(b"\r\n\r\n", 1)
        first, rest = head.decode().split("\r\n", 1)
        parsed = Parser().parsestr(rest)
        return int(first.split()[1]), json.loads(payload) if json_response else payload, parsed

    def login(self, username):
        code, _, headers = self.request("/api/login", {"username": username, "password": "strong-password"})
        self.assertEqual(code, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        code, me, _ = self.request("/api/me", cookie=cookie)
        self.assertEqual(code, 200)
        return cookie, me["csrf"]

    def test_complete_flow_and_atomic_claim(self):
        code, found, _ = self.request("/api/lookup", {"name": "Ana Silva", "email": "ana@example.org"})
        self.assertEqual(code, 200)
        self.assertNotIn("cpf", found)
        code, done, _ = self.request("/api/precheck", {"token": found["token"]})
        self.assertEqual((code, done["status"]), (200, "prechecked"))
        users = [self.login("vol1"), self.login("vol2")]
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]

        def claim(credentials):
            return self.request("/api/action/claim", {"id": pid}, *credentials)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, users))
        self.assertEqual(sorted(results), [200, 409])
        with app.connect() as db:
            row = db.execute("SELECT claimed_by FROM participants WHERE id=?", (pid,)).fetchone()
        owner = 0 if row["claimed_by"] == "vol1" else 1
        code, _, _ = self.request("/api/action/ready", {"id": pid}, *users[1-owner])
        self.assertEqual(code, 409)
        code, _, _ = self.request("/api/action/ready", {"id": pid}, *users[owner])
        self.assertEqual(code, 200)
        attendant = self.login("att1")
        code, queue, _ = self.request("/api/queue", cookie=attendant[0])
        self.assertEqual((code, queue["items"][0]["status"]), (200, "ready"))
        code, _, _ = self.request("/api/action/complete", {"id": pid}, *attendant)
        self.assertEqual(code, 200)
        code, panel, _ = self.request("/api/dashboard", cookie=self.login("admin")[0])
        self.assertEqual((panel["total"], panel["counts"]["completed"]), (2, 1))
        self.assertEqual(len(app.CSV.read_text(encoding="utf-8").splitlines()), 3)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE participant_id=?", (pid,)).fetchone()[0], 5)

    def test_public_lookup_requires_exact_name_and_email(self):
        code, _, _ = self.request("/api/lookup", {"name": "Ana Silva", "email": "wrong@example.org"})
        self.assertEqual(code, 404)
        code, _, _ = self.request("/api/dashboard")
        self.assertEqual(code, 401)

    def test_brand_assets_and_pages_are_served_locally(self):
        for path, expected_type in (("/", "text/html"), ("/app.css", "text/css"),
                                    ("/assets/bracis-2026-logo.png", "image/png"),
                                    ("/assets/bebas-neue.woff2", "font/woff2"),
                                    ("/assets/noto-sans.woff2", "font/woff2")):
            code, body, headers = self.request(path, json_response=False)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith(expected_type))
            self.assertTrue(body)
        self.assertIn(b"BRACIS 2026", self.request("/", json_response=False)[1])

    def test_reimport_changes_guiche_preserves_status(self):
        code, found, _ = self.request("/api/lookup", {"name": "Ana Silva", "email": "ana@example.org"})
        self.request("/api/precheck", {"token": found["token"]})
        input_file = Path(self.temp.name) / "updated.json"
        input_file.write_text(json.dumps([{"name": "Ána Silva", "email": "ana@example.org", "guiche": "7"}]))
        app.import_file(input_file)
        with app.connect() as db:
            row = db.execute("SELECT status,guiche FROM participants WHERE name_key='ana silva'").fetchone()
        self.assertEqual(tuple(row), ("prechecked", "7"))

    def test_sheet_outbox_is_acknowledged_only_after_success(self):
        app.SHEET_URL = "https://example.org/script"
        app.SHEET_SECRET = "test-secret"
        captured = []

        def fake_open(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(b'{"ok":true}')

        with patch.object(app.urllib.request, "urlopen", fake_open):
            self.assertEqual(app.sync_sheets_once(), 2)
        self.assertEqual(len(captured[0]["records"]), 2)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sheet_outbox WHERE delivered_at IS NULL").fetchone()[0], 0)

    def test_backup_is_consistent_and_does_not_overwrite(self):
        import sqlite3
        target = Path(self.temp.name) / "copy.sqlite3"
        app.backup_file(target)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            app.backup_file(target)


if __name__ == "__main__":
    unittest.main()
