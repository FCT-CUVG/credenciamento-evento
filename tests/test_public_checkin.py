"""Busca pública, pré-check-in e pendências do participante."""
import json
import unittest

from credenciamento import csv_io
from credenciamento import db as database
from support import CredenciamentoTestCase


class PublicCheckinTest(CredenciamentoTestCase):
    def test_public_lookup_accepts_cpf_or_name_and_email(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "123.456.789-01"})
        self.assertEqual((code, found["name"], found["affiliation"]), (200, "Á*a S***a", "I*******o A"))
        self.assertEqual(set(found), {"name", "affiliation", "token"})
        self.assertRegex(found["token"], r"^[A-Za-z0-9_-]{43}$")
        code, found, _ = self.request("/api/lookup", {"name": "Ána Silva", "email": "ana@example.org"})
        self.assertEqual((code, found["name"]), (200, "Á*a S***a"))
        code, done, _ = self.request("/api/precheck", {"token": found["token"]})
        self.assertEqual((code, done), (200, {"ok": True, "name": "Á*a S***a", "needs_guidance": False, "guiche": "1", "guiche_ranges": [{"from": "A", "to": "D"}], "guiche_priority": False}))
        self.assertEqual(self.request("/api/precheck", {"token": found["token"]})[0], 400)
        code, repeated, _ = self.request("/api/lookup", {"name": "Ána Silva", "email": "ana@example.org"})
        self.assertEqual((code, set(repeated)), (200, {"name", "affiliation", "token"}))
        self.assertEqual(self.request("/api/precheck", {"token": repeated["token"]})[0], 200)
        code, _, _ = self.request("/api/lookup", {"cpf": "123"})
        self.assertEqual(code, 400)
        code, _, _ = self.request("/api/lookup", {"name": "Ána Silva", "email": ""})
        self.assertEqual(code, 400)
        code, _, _ = self.request("/api/lookup", {"cpf": "00000000000"})
        self.assertEqual(code, 404)
        code, _, _ = self.request("/api/dashboard")
        self.assertEqual(code, 401)
        code, summary, _ = self.request("/api/dashboard/summary")
        self.assertEqual((code, summary), (200, {"total": 2, "arrived": 1, "completed": 0}))

    def test_unpaid_participant_is_directed_to_attendance(self):
        admin = self.login("admin")
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 0}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, False))
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.assertEqual(set(found), {"name", "affiliation", "token"})
        code, done, _ = self.request("/api/precheck", {"token": found["token"]})
        self.assertEqual((code, done), (200, {"ok": True, "name": "Á*a S***a", "needs_guidance": True}))
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 1)
        self.assertEqual(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"], [])
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 1}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, True))
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 0)
        self.assertEqual(len(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]), 1)

    def test_missing_affiliation_records_arrival_and_lists_pending_guidance(self):
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        previous_token = found["token"]
        csv_io.import_text(json.dumps([{"nome": "Ána Silva", "nome_cracha": "Ana",
                                     "email": "ana@example.org", "cpf": "12345678901",
                                     "pago": 1, "prioridade": 0}]), ".json")
        code, found, _ = self.request("/api/lookup", {"cpf": "12345678901"})
        self.assertEqual(code, 200)
        self.assertEqual(set(found), {"name", "affiliation", "token"})
        self.assertEqual(found["affiliation"], "")
        self.assertIn("token", found)
        code, result, _ = self.request("/api/precheck", {"token": previous_token})
        self.assertEqual((code, result["needs_guidance"]), (200, True))
        with database.connect() as db:
            row = db.execute("SELECT status, prechecked_at FROM participants WHERE email='ana@example.org'").fetchone()
            event = db.execute("SELECT action FROM events WHERE action='precheck_pending' AND participant_id=(SELECT id FROM participants WHERE email='ana@example.org')").fetchone()
        self.assertEqual(row["status"], "prechecked")
        self.assertTrue(row["prechecked_at"])
        self.assertEqual(event["action"], "precheck_pending")
        self.assertEqual(self.request("/pendencias", json_response=False)[0], 404)
        admin = self.login("admin")
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE email='ana@example.org'").fetchone()[0]
        self.assertEqual(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"], [])
        self.assertEqual(self.request("/api/dashboard/summary")[1]["arrived"], 1)
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 1)
        code, _, _ = self.request("/api/participants/status", {"id": pid, "status": "completed"}, *admin)
        self.assertEqual(code, 409)
        self.assertEqual(self.request("/api/participants/affiliation", {"id": pid, "affiliation": "X"},
                                      *self.login("vol1"))[0], 403)
        self.assertEqual(self.request("/api/participants/affiliation", {"id": pid, "affiliation": "  "}, *admin)[0], 400)
        code, updated, _ = self.request("/api/participants/affiliation",
                                        {"id": pid, "affiliation": "  Instituto   A "}, *admin)
        self.assertEqual((code, updated["item"]["affiliation"]), (200, "Instituto A"))
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE action='affiliation_update' AND actor='admin'").fetchone()[0], 1)
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 0)
        self.assertEqual(len(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]), 1)
        # Resolvida a pendência, o responsável credencia direto pelo painel detalhado.
        code, updated, _ = self.request("/api/participants/status", {"id": pid, "status": "completed"}, *admin)
        self.assertEqual((code, updated["item"]["status"]), (200, "completed"))


if __name__ == "__main__":
    unittest.main()
