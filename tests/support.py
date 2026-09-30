"""Base comum dos testes: banco e configuração em pasta temporária, requisições diretas ao
handler HTTP e login dos usuários de teste (vol1, vol2, att1, admin; senha strong-password)."""
import io
import json
import tempfile
import unittest
from email.message import Message
from email.parser import Parser
from pathlib import Path

from credenciamento import auth, csv_io, settings
from credenciamento import db as database
from credenciamento.bootstrap import init_db
from credenciamento.web.server import App

# Configurações trocadas em cada teste e restauradas no tearDown.
PATCHED_SETTINGS = ("DATA", "DB", "CSV", "SECRET", "SHEET_URL", "SHEET_SECRET", "RANGES")


class CredenciamentoTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_settings = {name: getattr(settings, name) for name in PATCHED_SETTINGS}
        settings.DATA = Path(self.temp.name) / "data"
        settings.DB = settings.DATA / "credenciamento.sqlite3"
        settings.CSV = settings.DATA / "participantes.csv"
        settings.SECRET = b"a-test-secret-that-is-longer-than-32-characters"
        settings.SHEET_URL = ""
        settings.SHEET_SECRET = ""
        auth.RATE.clear()
        settings.RANGES = Path(self.temp.name) / "guiches.json"
        settings.RANGES.write_text(json.dumps({"ranges": [
            {"from": "A", "to": "D", "guiche": "1"}, {"from": "E", "to": "H", "guiche": "2"},
            {"from": "I", "to": "M", "guiche": "3"}, {"from": "N", "to": "R", "guiche": "4"},
            {"from": "S", "to": "Z", "guiche": "5"}]}), encoding="utf-8")
        init_db()
        input_file = Path(self.temp.name) / "people.json"
        input_file.write_text(json.dumps([
            {"nome": "Ána Silva", "nome_cracha": "Ana", "afiliacao": "Instituto A",
             "email": "ana@example.org", "cpf": "12345678901", "pago": 1, "prioridade": 0},
            {"nome": "Bruno Lima", "nome_cracha": "Bruno", "afiliacao": "Instituto B",
             "email": "bruno@example.org", "cpf": "987.654.321-00", "pago": 1},
        ]), encoding="utf-8")
        csv_io.import_file(input_file)
        with database.connect() as db:
            for username, role, guiche in (("vol1", "volunteer", ""), ("vol2", "volunteer", ""),
                                            ("att1", "attendant", "1"), ("admin", "admin", "")):
                salt = "00" * 16
                db.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                           (username, salt, auth.password_hash("strong-password", salt), role, guiche))

    def tearDown(self):
        for name, value in self.original_settings.items():
            setattr(settings, name, value)
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
        handler = App.__new__(App)
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
