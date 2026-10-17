"""Busca de participantes no guichê, pedido de separação e filtro dos guichês gerais."""
import json
import unittest

from credenciamento import csv_io
from credenciamento import db as database
from support import CredenciamentoTestCase


class DeskSearchTest(CredenciamentoTestCase):
    def search(self, query, user="att1"):
        code, data, _ = self.request("/api/participants/search?q=" + query, cookie=self.login(user)[0])
        self.assertEqual(code, 200)
        return data

    def names(self, query, user="att1"):
        return [item["name"] for item in self.search(query, user)["items"]]

    def test_search_finds_anyone_by_name_affiliation_or_cpf(self):
        # Sem acento, em qualquer ordem e em nome ou afiliação; não só quem já fez o pré-check-in.
        self.assertEqual(self.names("ana"), ["Ána Silva"])
        self.assertEqual(self.names("silva ána"), ["Ána Silva"])
        self.assertEqual(self.names("instituto"), ["Ána Silva", "Bruno Lima"])
        self.assertEqual(self.names("instituto b"), ["Bruno Lima"])
        # Até 3 dígitos: início do CPF; CPF completo e e-mail comparam as chaves guardadas.
        self.assertEqual(self.names("987"), ["Bruno Lima"])
        self.assertEqual(self.names("98765"), [])
        self.assertEqual(self.names("123.456.789-01"), ["Ána Silva"])
        self.assertEqual(self.names("BRUNO@example.org", user="vol1"), ["Bruno Lima"])
        self.assertEqual(self.search("a"), {"items": [], "total": 0})
        item = self.search("bruno")["items"][0]
        self.assertEqual((item["affiliation"], item["cpf_prefix"], item["status"], item["pending"]),
                         ("Instituto B", "987", "registered", False))
        # A busca da equipe também não devolve CPF nem e-mail.
        self.assertFalse({"cpf", "email", "cpf_key", "email_key"} & set(item))
        self.assertEqual(self.request("/api/participants/search?q=ana")[0], 401)

    def test_desk_requests_separation_for_who_did_not_precheck(self):
        attendant = self.login("att1")
        pid = self.pid_by_email("bruno@example.org")
        code, result, _ = self.request("/api/action/request", {"id": pid}, *attendant)
        self.assertEqual((code, result["item"]["status"], result["item"]["pending"]), (200, "prechecked", False))
        queue = self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]
        self.assertEqual([item["id"] for item in queue], [pid])
        # Só vale para quem ainda não chegou; o público passa a ver que o pré-check-in já foi feito.
        self.assertEqual(self.request("/api/action/request", {"id": pid}, *attendant)[0], 409)
        self.assertEqual(self.request("/api/checkin", {"cpf": "98765432100"})[0], 409)
        with database.connect() as db:
            event = db.execute("SELECT action, actor FROM events WHERE participant_id=? ORDER BY rowid DESC",
                               (pid,)).fetchone()
        self.assertEqual(tuple(event), ("request", "att1"))
        # Pedido por engano: dá para desfazer enquanto ninguém assumiu a busca.
        code, result, _ = self.request("/api/action/undo_request", {"id": pid}, *attendant)
        self.assertEqual((code, result["item"]["status"], result["item"]["prechecked_at"]), (200, "registered", None))
        self.assertEqual(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"], [])
        self.assertEqual(self.request("/api/action/request", {"id": pid}, *attendant)[0], 200)
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *self.login("vol1"))[0], 200)
        self.assertEqual(self.request("/api/action/undo_request", {"id": pid}, *attendant)[0], 409)

    def test_pending_registration_is_flagged_and_stays_out_of_the_queue(self):
        csv_io.import_text(json.dumps([{"nome": "Ána Silva", "email": "ana@example.org", "cpf": "12345678901",
                                        "pago": 0, "afiliacao": "Instituto A"}]), ".json")
        item = self.search("ana")["items"][0]
        self.assertTrue(item["pending"])
        pid = item["id"]
        code, result, _ = self.request("/api/action/request", {"id": pid}, *self.login("att1"))
        self.assertEqual((code, result["item"]["status"], result["item"]["pending"]), (200, "prechecked", True))
        # Chegada registrada, mas sem entrar na separação: a coordenação orienta primeiro.
        self.assertEqual(self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"], [])
        self.assertEqual(self.request("/api/dashboard", cookie=self.login("admin")[0])[1]["guidance_pending"], 1)
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *self.login("vol1"))[0], 409)

    def test_general_desks_view_leaves_out_the_priority_desk(self):
        admin = self.login("admin")
        ranges = self.request("/api/guiches/config", cookie=admin[0])[1]["ranges"]
        self.request("/api/guiches/config", {"ranges": ranges, "priority_guiche": "Prioridade"}, *admin)
        self.request("/api/participants/priority", {"id": self.pid_by_email("ana@example.org"), "priority": 1}, *admin)
        for cpf in ("12345678901", "98765432100"):
            self.assertEqual(self.request("/api/checkin", {"cpf": cpf})[0], 200)
        volunteer = self.login("vol1")
        for email in ("ana@example.org", "bruno@example.org"):
            for action in ("claim", "ready"):
                self.request("/api/action/" + action, {"id": self.pid_by_email(email)}, *volunteer)

        def desks(query):
            data = self.request("/api/queue?view=attendant&" + query, cookie=volunteer[0])[1]
            return sorted(item["guiche"] for item in data["items"])
        self.assertEqual(desks("guiche=all"), ["1", "Prioridade"])
        self.assertEqual(desks("guiche=all&exclude=priority"), ["1"])
        self.assertEqual(desks("guiche=Prioridade"), ["Prioridade"])
        # Num guichê específico, o filtro não muda nada.
        self.assertEqual(desks("guiche=Prioridade&exclude=priority"), ["Prioridade"])


if __name__ == "__main__":
    unittest.main()
