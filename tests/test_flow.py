import json
import io
import csv
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
            {"nome": "Ána Silva", "nome_cracha": "Ana", "afiliacao": "Instituto A",
             "email": "ana@example.org", "cpf": "12345678901", "pago": 1, "prioridade": 1},
            {"nome": "Bruno Lima", "nome_cracha": "Bruno", "afiliacao": "Instituto B",
             "email": "bruno@example.org", "cpf": "987.654.321-00", "pago": 1},
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
        handler.command = "POST" if data is not None else "GET"
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
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
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
        code, guiche_queue, _ = self.request("/api/queue?view=attendant", cookie=attendant[0])
        self.assertEqual((code, [item["status"] for item in guiche_queue["items"]]), (200, ["completed"]))
        self.assertEqual(guiche_queue["items"][0]["id"], pid)
        code, volunteer_queue, _ = self.request("/api/queue", cookie=users[owner][0])
        self.assertEqual((code, volunteer_queue["items"]), (200, []))
        code, panel, _ = self.request("/api/dashboard", cookie=self.login("admin")[0])
        self.assertEqual((panel["total"], panel["counts"]["completed"]), (2, 1))
        code, summary, _ = self.request("/api/dashboard/summary")
        self.assertEqual((code, summary), (200, {"total": 2, "arrived": 1, "completed": 1}))
        self.assertEqual(len(app.CSV.read_text(encoding="utf-8").splitlines()), 3)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE participant_id=?", (pid,)).fetchone()[0], 5)

    def test_import_requires_new_fields_and_valid_cpf(self):
        required = {"nome": "Carla Souza", "nome_cracha": "Carla", "afiliacao": "Instituto C",
                    "email": "carla@example.org", "cpf": "12345678901"}
        missing_badge = dict(required)
        del missing_badge["nome_cracha"]
        with self.assertRaisesRegex(ValueError, "nome_cracha"):
            app.import_text(json.dumps([missing_badge]), ".json")
        invalid_cpf = dict(required, cpf="123")
        with self.assertRaisesRegex(ValueError, "cpf deve ter 11"):
            app.import_text(json.dumps([invalid_cpf]), ".json")

    def test_unpaid_participant_is_directed_to_attendance(self):
        admin = self.login("admin")
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 0}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, False))
        code, result, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 403)
        self.assertIn("Procure atendimento", result["error"])
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 1}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, True))
        self.assertEqual(self.request("/api/lookup", {"cpf": "12345678901"})[0], 200)

    def test_public_lookup_requires_cpf(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "123.456.789-01"})
        self.assertEqual((code, found["name"]), (200, "Ána Silva"))
        code, _, _ = self.request("/api/lookup", {"cpf": "123"})
        self.assertEqual(code, 400)
        code, _, _ = self.request("/api/lookup", {"cpf": "00000000000"})
        self.assertEqual(code, 404)
        code, _, _ = self.request("/api/dashboard")
        self.assertEqual(code, 401)
        code, summary, _ = self.request("/api/dashboard/summary")
        self.assertEqual((code, summary), (200, {"total": 2, "arrived": 0, "completed": 0}))

    def test_attendant_can_view_other_desks_but_only_complete_own(self):
        input_file = Path(self.temp.name) / "extra-desk.json"
        input_file.write_text(json.dumps([
            {"nome": "Bruno Lima", "nome_cracha": "Bruno", "afiliacao": "Instituto B",
             "email": "bruno@example.org", "cpf": "987.654.321-00", "pago": 1, "guiche": "7"}
        ]), encoding="utf-8")
        app.import_file(input_file)
        attendant = self.login("att1")
        code, desks, _ = self.request("/api/guiches", cookie=attendant[0])
        self.assertEqual(code, 200)
        self.assertIn({"id": "7", "ranges": []}, desks["guiches"])
        code, found, _ = self.request("/api/lookup", {"cpf": "98765432100"})
        self.assertEqual(code, 200)
        self.request("/api/precheck", {"token": found["token"]})
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='bruno lima'").fetchone()[0]
        volunteer = self.login("vol1")
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *volunteer)[0], 200)
        self.assertEqual(self.request("/api/action/ready", {"id": pid}, *volunteer)[0], 200)
        code, queue, _ = self.request("/api/queue?guiche=7", cookie=attendant[0])
        self.assertEqual((code, [item["id"] for item in queue["items"]]), (200, [pid]))
        self.assertEqual(self.request("/api/action/complete", {"id": pid}, *attendant)[0], 409)
        self.assertEqual(self.request("/api/action/undo_ready", {"id": pid}, *attendant)[0], 409)

    def test_volunteer_can_confirm_pickup_at_guiche(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.assertEqual(self.request("/api/precheck", {"token": found["token"]})[0], 200)
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        separator = self.login("vol1")
        desk_volunteer = self.login("vol2")
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *separator)[0], 200)
        self.assertEqual(self.request("/api/action/ready", {"id": pid}, *separator)[0], 200)
        code, queue, _ = self.request("/api/queue?view=attendant&guiche=1", cookie=desk_volunteer[0])
        self.assertEqual((code, [item["id"] for item in queue["items"]]), (200, [pid]))
        self.assertEqual(self.request("/api/action/complete", {"id": pid}, *desk_volunteer)[0], 200)
        self.assertEqual(self.request("/api/action/complete", {"id": pid}, *desk_volunteer)[0], 409)
        code, queue, _ = self.request("/api/queue?view=attendant&guiche=1", cookie=desk_volunteer[0])
        self.assertEqual((code, [item["status"] for item in queue["items"]]), (200, ["completed"]))
        self.assertEqual(self.request("/api/action/undo_complete", {"id": pid}, *desk_volunteer)[0], 200)
        self.assertEqual(self.request("/api/action/undo_complete", {"id": pid}, *desk_volunteer)[0], 409)
        self.assertEqual(self.request("/api/action/undo_ready", {"id": pid}, *separator)[0], 200)
        with app.connect() as db:
            row = db.execute("SELECT status,claimed_by,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
            actions = [event[0] for event in db.execute("SELECT action FROM events WHERE participant_id=? ORDER BY occurred_at", (pid,))]
        self.assertEqual(tuple(row), ("searching", "vol1", None, None))
        self.assertIn("undo_complete", actions)
        self.assertIn("undo_ready", actions)

    def test_admin_can_adjust_participant_status_and_timestamps(self):
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        self.assertEqual(self.request("/api/participants/status", {"id": pid, "status": "completed"})[0], 401)
        self.assertEqual(self.request("/api/participants/status", {"id": pid, "status": "completed"}, *self.login("vol1"))[0], 403)
        code, result, _ = self.request("/api/participants/status", {"id": pid, "status": "completed"}, *self.login("admin"))
        self.assertEqual((code, result["item"]["status"]), (200, "completed"))
        with app.connect() as db:
            row = db.execute("SELECT prechecked_at,claimed_at,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
        self.assertTrue(all(row))
        code, result, _ = self.request("/api/participants/status", {"id": pid, "status": "registered"}, *self.login("admin"))
        self.assertEqual((code, result["item"]["status"]), (200, "registered"))
        with app.connect() as db:
            row = db.execute("SELECT prechecked_at,claimed_at,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
            actions = [event[0] for event in db.execute("SELECT action FROM events WHERE participant_id=? ORDER BY occurred_at", (pid,))]
        self.assertEqual(tuple(row), (None, None, None, None))
        self.assertIn("status_completed", actions)
        self.assertIn("status_registered", actions)

    def test_brand_assets_and_pages_are_served_locally(self):
        event = app.event_config()
        for path, expected_type in (("/", "text/html"), ("/painel/resumo", "text/html"),
                                    ("/app.css", "text/css"),
                                    (f"/assets/{event['logo']}", "image/png"),
                                    (f"/assets/{event['decoration']}", "image/svg+xml"),
                                    ("/assets/bebas-neue.woff2", "font/woff2"),
                                    ("/assets/noto-sans.woff2", "font/woff2")):
            code, body, headers = self.request(path, json_response=False)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith(expected_type))
            self.assertTrue(body)
        self.assertIn(event["name"].encode(), self.request("/", json_response=False)[1])

    def test_reimport_changes_guiche_preserves_status(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.request("/api/precheck", {"token": found["token"]})
        input_file = Path(self.temp.name) / "updated.json"
        input_file.write_text(json.dumps([{"nome": "Ána Silva", "nome_cracha": "Ana", "afiliacao": "Instituto A",
                            "email": "ana@example.org", "cpf": "12345678901", "pago": 1,
                            "prioridade": 1, "guiche": "7"}]))
        app.import_file(input_file)
        with app.connect() as db:
            row = db.execute("SELECT status,guiche FROM participants WHERE name_key='ana silva'").fetchone()
        self.assertEqual(tuple(row), ("prechecked", "7"))

    def test_event_yaml_changes_theme_and_public_brand(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        current = "\n".join(line for line in app.EVENT_CONFIG.read_text(encoding="utf-8").splitlines()
                            if not line.startswith("registration_hint_")) + "\n"
        event = app.event_config()
        config_file.write_text(current.replace(event["name"], "Encontro Exemplo")
                          .replace(f"short_name: {event['short_name']}", "short_name: Encontro")
                          .replace('"#313267"', '"#123456"'), encoding="utf-8")
        with patch.object(app, "EVENT_CONFIG", config_file):
            code, event, _ = self.request("/api/event")
            self.assertEqual((code, event["name"], event["short_name"]),
                             (200, "Encontro Exemplo", "Encontro"))
            self.assertEqual(event["registration_hints"], {"en": None, "pt-BR": None})
            code, css, headers = self.request("/theme.css", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"--primary: #123456", css)
            self.assertTrue(headers["Content-Type"].startswith("text/css"))
            code, page, _ = self.request("/", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"Encontro Exemplo", page)
            self.assertIn(b"ARRIVED AT Encontro?", page)
            self.assertNotIn(b"ECOS", page)
            self.assertNotIn(b"{{EVENT_NAME}}", page)
            for route in ("/busca", "/fila", "/painel"):
                code, team_page, _ = self.request(route, json_response=False)
                self.assertEqual(code, 200)
                self.assertNotIn(b'class="page-context"', team_page)
            self.assertEqual(self.request("/assets/unlisted.svg", json_response=False)[0], 404)

    def test_event_yaml_allows_missing_decoration(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        current = app.EVENT_CONFIG.read_text(encoding="utf-8")
        event = app.event_config()
        config_file.write_text(current.replace(f"decoration: {event['decoration']}\n", ""), encoding="utf-8")
        with patch.object(app, "EVENT_CONFIG", config_file):
            self.assertNotIn("decoration", app.event_config())
            code, css, _ = self.request("/theme.css", json_response=False)
            self.assertEqual(code, 200)
            self.assertIn(b"--event-decoration: none", css)
            self.assertIn(b"--event-decoration-opacity: 0", css)
            self.assertEqual(self.request(f"/assets/{event['decoration']}", json_response=False)[0], 404)

    def test_event_yaml_rejects_unsafe_asset_and_bad_color(self):
        config_file = Path(self.temp.name) / "evento.yaml"
        original = app.EVENT_CONFIG.read_text(encoding="utf-8")
        event = app.event_config()
        with patch.object(app, "EVENT_CONFIG", config_file):
            config_file.write_text(original.replace(event["logo"], "../outside.png"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "logo"):
                app.event_config()
            config_file.write_text(original.replace('"#313267"', '"red; background:url(evil)"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "color"):
                app.event_config()

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

    def test_admin_exports_and_reimports_participants_without_resetting_status(self):
        self.assertEqual(self.request("/api/participants/export", json_response=False)[0], 401)
        self.assertEqual(self.request("/api/events/export", json_response=False)[0], 401)
        self.assertEqual(self.request("/api/google-sheets/script")[0], 401)
        self.assertEqual(self.request("/api/participants/export", cookie=self.login("vol1")[0], json_response=False)[0], 401)
        admin = self.login("admin")
        code, script, _ = self.request("/api/google-sheets/script", cookie=admin[0])
        self.assertEqual(code, 200)
        self.assertIn("function doPost", script["script"])
        self.assertEqual(self.request("/api/participants/template", json_response=False)[0], 401)
        code, template, headers = self.request("/api/participants/template", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        self.assertIn("participantes-exemplo.csv", headers["Content-Disposition"])
        self.assertEqual(list(csv.DictReader(io.StringIO(template.decode("utf-8-sig"))))[0]["nome_cracha"], "Maria")
        code, payload, headers = self.request("/api/participants/export", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        self.assertIn("dados-participantes.csv", headers["Content-Disposition"])
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), set(app.PARTICIPANT_EXPORT_COLUMNS))
        self.assertIn("situacao", rows[0])

        code, payload, headers = self.request("/api/events/export", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        self.assertIn("logs-movimentacoes.csv", headers["Content-Disposition"])
        logs = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(set(logs[0]), set(app.EVENT_LOG_COLUMNS))
        self.assertEqual({row["acao"] for row in logs}, {"import"})

        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.assertEqual(self.request("/api/precheck", {"token": found["token"]})[0], 200)
        original_id = next(row["id"] for row in rows if row["email"] == "ana@example.org")
        for row in rows:
            if row["id"] == original_id:
                row["nome"] = "Ana Silva Nova"
                row["afiliacao"] = "Instituto Atualizado"
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=app.PARTICIPANT_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        data = {"filename": "participantes.csv", "content": output.getvalue()}
        self.assertEqual(self.request("/api/participants/import", data, *self.login("vol1"))[0], 403)
        self.assertEqual(self.request("/api/participants/import", data, cookie=admin[0])[0], 403)
        code, result, _ = self.request("/api/participants/import", data, *admin)
        self.assertEqual((code, result), (200, {"read": 2, "changed": 1}))
        self.assertEqual(self.request("/api/participants/import", data, *admin)[1]["changed"], 0)
        with app.connect() as db:
            row = db.execute("SELECT id,status,name,affiliation FROM participants WHERE id=?", (original_id,)).fetchone()
            actor = db.execute("SELECT actor FROM events WHERE participant_id=? AND action='import_update'", (original_id,)).fetchone()[0]
        self.assertEqual(tuple(row), (original_id, "prechecked", "Ana Silva Nova", "Instituto Atualizado"))
        self.assertEqual(actor, "admin")

    def test_web_import_is_atomic_when_active_desk_would_change(self):
        admin = self.login("admin")
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.request("/api/precheck", {"token": found["token"]})
        with app.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        volunteer = self.login("vol1")
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *volunteer)[0], 200)
        content = ("nome,nome_cracha,afiliacao,email,cpf,pago,prioridade,guiche\n"
               "Nova Pessoa,Nova Pessoa,Instituto Novo,nova@example.org,11111111111,0,0,1\n"
               "Ána Silva,Ana,Instituto A,ana@example.org,12345678901,1,1,2\n")
        code, _, _ = self.request("/api/participants/import", {"filename": "lista.csv", "content": content}, *admin)
        self.assertEqual(code, 400)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT guiche FROM participants WHERE id=?", (pid,)).fetchone()[0], "1")

    def test_admin_configures_non_overlapping_guiche_ranges(self):
        ranges_file = Path(self.temp.name) / "guiches.json"
        ranges_file.write_text(app.RANGES.read_text(encoding="utf-8"), encoding="utf-8")
        admin = self.login("admin")
        with patch.object(app, "RANGES", ranges_file):
            self.assertEqual(self.request("/api/guiches/config")[0], 401)
            code, config, _ = self.request("/api/guiches/config", cookie=admin[0])
            self.assertEqual((code, len(config["ranges"])), (200, 5))
            invalid = [dict(item) for item in config["ranges"]]
            invalid[0]["to"] = "E"
            self.assertEqual(self.request("/api/guiches/config", {"ranges": invalid}, *admin)[0], 400)
            self.assertEqual(self.request("/api/guiches/config", {"ranges": config["ranges"]}, *self.login("vol1"))[0], 403)
            valid = [dict(item) for item in config["ranges"]]
            valid[0]["to"] = "C"
            valid[1]["from"] = "D"
            code, updated, _ = self.request("/api/guiches/config", {"ranges": valid}, *admin)
            self.assertEqual((code, updated["ranges"]), (200, valid))
            self.assertEqual(app.guiche_for("Daniel"), "2")
            self.assertEqual(app.guiche_for("Ana"), "1")


if __name__ == "__main__":
    unittest.main()
