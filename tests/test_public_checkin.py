"""Busca pública, pré-check-in e pendências do participante."""
import json
import unittest

from credenciamento import csv_io, lookup, settings
from credenciamento import db as database
from support import CredenciamentoTestCase


class PublicCheckinTest(CredenciamentoTestCase):
    def test_public_checkin_by_cpf_or_email_records_arrival_in_one_step(self):
        done_ana = {"ok": True, "name": "Á*a S***a", "needs_guidance": False, "guiche": "1",
                    "guiche_ranges": [{"from": "A", "to": "D"}], "guiche_priority": False}
        code, done, _ = self.request("/api/checkin", {"cpf": "123.456.789-01"})
        self.assertEqual((code, done), (200, done_ana))
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM participants WHERE email_key=?", (lookup.email_key("ana@example.org"),)).fetchone()[0],
                             "prechecked")
        # Repetir a busca (ou buscar pelo e-mail) mostra o mesmo guichê sem registrar de novo.
        code, done, _ = self.request("/api/checkin", {"email": " ANA@example.org "})
        self.assertEqual((code, done), (200, done_ana))
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE action='precheck'").fetchone()[0], 1)
        for invalid in ({"cpf": "123"}, {"email": "ana"}, {}, {"name": "Ána Silva"}):
            self.assertEqual(self.request("/api/checkin", invalid)[0], 400)
        self.assertEqual(self.request("/api/checkin", {"cpf": "00000000000"})[0], 404)
        self.assertEqual(self.request("/api/checkin", {"email": "ninguem@example.org"})[0], 404)
        # Duas inscrições com o mesmo e-mail: não dá para saber qual é a da pessoa.
        csv_io.import_text(json.dumps([{"nome": "Beatriz Lima", "email": "bruno@example.org", "pago": 1}]), ".json")
        self.assertEqual(self.request("/api/checkin", {"email": "bruno@example.org"})[0], 409)
        self.assertEqual(self.request("/api/checkin", {"cpf": "98765432100"})[0], 200)
        for old_route in ("/api/lookup", "/api/precheck"):
            self.assertEqual(self.request(old_route, {"cpf": "12345678901"})[0], 404)
        code, _, _ = self.request("/api/dashboard")
        self.assertEqual(code, 401)
        code, summary, _ = self.request("/api/dashboard/summary")
        self.assertEqual((code, summary), (200, {"total": 3, "arrived": 2, "completed": 0}))

    def test_unpaid_participant_is_directed_to_attendance(self):
        admin = self.login("admin")
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 0}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, False))
        code, done, _ = self.request("/api/checkin", {"cpf": "12345678901"})
        self.assertEqual((code, done), (200, {"ok": True, "name": "Á*a S***a", "needs_guidance": True}))
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 1)
        self.assertEqual(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"], [])
        code, updated, _ = self.request("/api/participants/payment", {"id": pid, "paid": 1}, *admin)
        self.assertEqual((code, updated["item"]["paid"]), (200, True))
        self.assertEqual(self.request("/api/dashboard", cookie=admin[0])[1]["guidance_pending"], 0)
        self.assertEqual(len(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]), 1)

    def test_missing_affiliation_records_arrival_and_lists_pending_guidance(self):
        csv_io.import_text(json.dumps([{"nome": "Ána Silva", "nome_cracha": "Ana",
                                     "email": "ana@example.org", "cpf": "12345678901",
                                     "pago": 1, "prioridade": 0}]), ".json")
        code, result, _ = self.request("/api/checkin", {"cpf": "12345678901"})
        self.assertEqual((code, result), (200, {"ok": True, "name": "Á*a S***a", "needs_guidance": True}))
        with database.connect() as db:
            row = db.execute("SELECT status, prechecked_at FROM participants WHERE email_key=?", (lookup.email_key("ana@example.org"),)).fetchone()
            event = db.execute("SELECT action FROM events WHERE action='precheck_pending' AND participant_id=(SELECT id FROM participants WHERE email_key=?)",
                               (lookup.email_key("ana@example.org"),)).fetchone()
        self.assertEqual(row["status"], "prechecked")
        self.assertTrue(row["prechecked_at"])
        self.assertEqual(event["action"], "precheck_pending")
        self.assertEqual(self.request("/pendencias", json_response=False)[0], 404)
        admin = self.login("admin")
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE email_key=?", (lookup.email_key("ana@example.org"),)).fetchone()[0]
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

    def test_public_checkin_opens_and_closes_from_the_dashboard(self):
        admin = self.login("admin")
        self.assertEqual(self.request("/api/public-checkin", {"open": False}, *self.login("vol1"))[0], 403)
        for invalid in ({}, {"open": "0"}):
            self.assertEqual(self.request("/api/public-checkin", invalid, *admin)[0], 400)
        code, result, _ = self.request("/api/public-checkin", {"open": False}, *admin)
        self.assertEqual((code, result["public_checkin"]["open"], result["public_checkin"]["by"]), (200, False, "admin"))
        self.assertFalse(self.request("/api/event")[1]["checkin_open"])
        code, closed, _ = self.request("/api/checkin", {"cpf": "12345678901"})
        self.assertEqual((code, closed["closed"]), (403, True))
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM participants WHERE name_key='ana silva'").fetchone()[0],
                             "registered")
        self.assertFalse(self.request("/api/dashboard", cookie=admin[0])[1]["public_checkin"]["open"])
        self.request("/api/public-checkin", {"open": True}, *admin)
        self.assertTrue(self.request("/api/event")[1]["checkin_open"])
        self.assertEqual(self.request("/api/checkin", {"cpf": "12345678901"})[0], 200)

    def test_only_searches_without_result_are_limited_and_counted_for_the_alert(self):
        settings.LIMITS.update(search="100/1", search_miss="3/10", alert_miss="2/10")
        # Quem encontra a inscrição não gasta o limite de erros, mesmo repetindo a busca.
        for _ in range(5):
            self.assertEqual(self.request("/api/checkin", {"cpf": "12345678901"})[0], 200)
        for cpf in ("00000000001", "00000000002", "00000000003"):
            self.assertEqual(self.request("/api/checkin", {"cpf": cpf})[0], 404)
        # Depois de três erros, o mesmo IP fica bloqueado por um tempo, até para uma busca certa.
        self.assertEqual(self.request("/api/checkin", {"cpf": "98765432100"})[0], 429)
        misses = self.request("/api/dashboard", cookie=self.login("admin")[0])[1]["search_misses"]
        self.assertEqual(misses, {"count": 3, "alert": 2, "minutes": 10})
        settings.LIMITS.update(search="2/1", search_miss="20/10")
        self.assertEqual(self.request("/api/checkin", {"cpf": "12345678901"})[0], 429)

    def test_dashboard_finds_people_by_cpf_or_email_without_receiving_them(self):
        admin = self.login("admin")
        items = self.request("/api/dashboard", cookie=admin[0])[1]["items"]
        self.assertFalse(any("email" in item or "cpf" in item for item in items))
        self.assertEqual({(item["cpf_prefix"], item["has_cpf"]) for item in items}, {("123", True), ("987", True)})
        ana = self.pid_by_email("ana@example.org")
        for query in ("123.456.789-01", "12345678901", " ANA@example.org "):
            code, found, _ = self.request("/api/participants/find", {"query": query}, *admin)
            self.assertEqual((code, found["ids"]), (200, [ana]), query)
        self.assertEqual(self.request("/api/participants/find", {"query": "ninguem@example.org"}, *admin)[1]["ids"], [])
        self.assertEqual(self.request("/api/participants/find", {"query": "Ana"}, *admin)[0], 400)
        self.assertEqual(self.request("/api/participants/find", {"query": "12345678901"}, *self.login("vol1"))[0], 403)


if __name__ == "__main__":
    unittest.main()
