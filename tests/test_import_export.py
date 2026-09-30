"""Importação, reimportação e exportações."""
import csv
import io
import json
import unittest
from pathlib import Path

from credenciamento import common, csv_io
from credenciamento import db as database
from support import CredenciamentoTestCase


class ImportExportTest(CredenciamentoTestCase):
    def test_import_allows_missing_badge_affiliation_and_cpf(self):
        required = {"nome": "Carla Souza", "nome_cracha": "Carla", "afiliacao": "Instituto C",
                    "email": "carla@example.org", "cpf": "12345678901"}
        missing_badge = dict(required)
        del missing_badge["nome_cracha"]
        del missing_badge["afiliacao"]
        missing_badge["nome"] = "Carla Maria Souza"
        missing_badge["cpf"] = ""
        csv_io.import_text(json.dumps([missing_badge]), ".json")
        with database.connect() as db:
            row = db.execute("SELECT badge_name, affiliation, cpf FROM participants WHERE email='carla@example.org'").fetchone()
        self.assertEqual(tuple(row), ("Carla Souza", "", ""))
        invalid_cpf = dict(required, cpf="123")
        with self.assertRaisesRegex(ValueError, "cpf deve ter 11"):
            csv_io.import_text(json.dumps([invalid_cpf]), ".json")
        csv_io.import_text(json.dumps([{"nome": "Madonna", "email": "madonna@example.org"}]), ".json")
        with database.connect() as db:
            row = db.execute("SELECT badge_name FROM participants WHERE email='madonna@example.org'").fetchone()
        self.assertEqual(row[0], "Madonna")

    def test_import_csv_accepts_spreadsheet_delimiters_and_skips_empty_rows(self):
        for delimiter in (",", ";", "\t"):
            with self.subTest(delimiter=delimiter):
                content = delimiter.join(("nome", "afiliacao", "pago", "nome_cracha", "email", "cpf")) + "\n"
                content += delimiter.join(("Carla Souza", "Instituto C", "1", "Carla", "carla@example.org", "")) + "\n"
                content += delimiter * 5 + "\n"
                self.assertEqual(csv_io.import_text(content, ".csv"), (1, 1 if delimiter == "," else 0))
        with self.assertRaisesRegex(ValueError, "não contém participantes"):
            csv_io.import_text("nome,afiliacao,pago,nome_cracha,email,cpf\n,,,,,\n", ".csv")
        with self.assertRaisesRegex(ValueError, "Cabeçalho CSV inválido"):
            csv_io.import_text("nome;afiliacao;pago;cpf\nCarla;Instituto C;1;\n", ".csv")
        csv_io.import_text("nome;email\nJoana da Silva;joana@example.org\n", ".csv")
        with database.connect() as db:
            row = db.execute("SELECT badge_name, affiliation FROM participants WHERE email='joana@example.org'").fetchone()
        self.assertEqual(tuple(row), ("Joana Silva", ""))

    def test_reimport_changes_guiche_preserves_status(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.request("/api/precheck", {"token": found["token"]})
        input_file = Path(self.temp.name) / "updated.json"
        input_file.write_text(json.dumps([{"nome": "Ána Silva", "nome_cracha": "Ana", "afiliacao": "Instituto A",
                            "email": "ana@example.org", "cpf": "12345678901", "pago": 1,
                            "prioridade": 0, "guiche": "7"}]))
        csv_io.import_file(input_file)
        with database.connect() as db:
            row = db.execute("SELECT status,guiche FROM participants WHERE name_key='ana silva'").fetchone()
        self.assertEqual(tuple(row), ("prechecked", "7"))

    def test_reimport_by_id_updates_lookup_keys(self):
        original = {"nome": "Carla Souza", "nome_cracha": "Carla", "afiliacao": "Instituto C",
                    "email": "carla@example.org", "pago": 1, "guiche": "1"}
        self.assertEqual(csv_io.import_text(json.dumps([original]), ".json"), (1, 1))
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key=? AND email_key=?",
                             (common.normalize(original["nome"]), original["email"].casefold())).fetchone()[0]

        updated = dict(original, id=pid, nome="Cárla Oliveira", email="Carla.Nova@Example.org")
        self.assertEqual(csv_io.import_text(json.dumps([updated]), ".json"), (1, 1))
        with database.connect() as db:
            rows = db.execute("SELECT id, name, email FROM participants WHERE name_key=? AND email_key=?",
                              (common.normalize(updated["nome"]), updated["email"].casefold())).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(pid, updated["nome"], updated["email"])])
        code, found, _ = self.request("/api/lookup", {"name": "Carla Oliveira", "email": "carla.nova@example.org"})
        self.assertEqual((code, found["name"]), (200, "C***a O******a"))

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
        self.assertEqual(set(rows[0]), set(csv_io.PARTICIPANT_EXPORT_COLUMNS))
        self.assertIn("situacao", rows[0])

        code, payload, headers = self.request("/api/events/export", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        self.assertIn("logs-movimentacoes.csv", headers["Content-Disposition"])
        logs = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(set(logs[0]), set(csv_io.EVENT_LOG_COLUMNS))
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
        writer = csv.DictWriter(output, fieldnames=csv_io.PARTICIPANT_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        data = {"filename": "participantes.csv", "content": output.getvalue()}
        self.assertEqual(self.request("/api/participants/import", data, *self.login("vol1"))[0], 403)
        self.assertEqual(self.request("/api/participants/import", data, cookie=admin[0])[0], 403)
        code, result, _ = self.request("/api/participants/import", data, *admin)
        self.assertEqual((code, result), (200, {"read": 2, "changed": 1}))
        self.assertEqual(self.request("/api/participants/import", data, *admin)[1]["changed"], 0)
        with database.connect() as db:
            row = db.execute("SELECT id,status,name,affiliation FROM participants WHERE id=?", (original_id,)).fetchone()
            actor = db.execute("SELECT actor FROM events WHERE participant_id=? AND action='import_update'", (original_id,)).fetchone()[0]
        self.assertEqual(tuple(row), (original_id, "prechecked", "Ana Silva Nova", "Instituto Atualizado"))
        self.assertEqual(actor, "admin")

    def test_web_import_is_atomic_when_active_desk_would_change(self):
        admin = self.login("admin")
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.request("/api/precheck", {"token": found["token"]})
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        volunteer = self.login("vol1")
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *volunteer)[0], 200)
        content = ("nome,nome_cracha,afiliacao,email,cpf,pago,prioridade,guiche\n"
               "Nova Pessoa,Nova Pessoa,Instituto Novo,nova@example.org,11111111111,0,0,1\n"
               "Ána Silva,Ana,Instituto A,ana@example.org,12345678901,1,1,2\n")
        code, _, _ = self.request("/api/participants/import", {"filename": "lista.csv", "content": content}, *admin)
        self.assertEqual(code, 400)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT guiche FROM participants WHERE id=?", (pid,)).fetchone()[0], "1")


if __name__ == "__main__":
    unittest.main()
