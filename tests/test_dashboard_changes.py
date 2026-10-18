"""País, situações de pagamento configuráveis e alteração em lote pelo painel."""
import csv
import io
import json
import unittest

from credenciamento import csv_io
from credenciamento import db as database
from support import CredenciamentoTestCase


class DashboardChangesTest(CredenciamentoTestCase):
    def import_people(self, people):
        return csv_io.import_text(json.dumps(people), ".json")

    def items(self, admin):
        return {item["name"]: item for item in self.request("/api/dashboard", cookie=admin[0])[1]["items"]}

    def test_country_is_optional_and_kept_when_the_column_is_missing(self):
        self.import_people([{"nome": "Ána Silva", "email": "ana@example.org", "pais": " Brasil ", "pago": 1},
                            {"nome": "Bruno Lima", "email": "bruno@example.org", "pago": 1}])
        admin = self.login("admin")
        items = self.items(admin)
        self.assertEqual((items["Ána Silva"]["country"], items["Bruno Lima"]["country"]), ("Brasil", ""))
        # Lista sem a coluna pais não apaga o país já cadastrado.
        self.import_people([{"nome": "Ána Silva", "email": "ana@example.org", "pago": 1}])
        self.assertEqual(self.items(admin)["Ána Silva"]["country"], "Brasil")
        # Exporta e reimporta com o país.
        exported = self.request("/api/participants/export", cookie=admin[0], json_response=False)[1]
        rows = list(csv.DictReader(io.StringIO(exported.decode("utf-8-sig"))))
        self.assertEqual({row["nome"]: row["pais"] for row in rows}, {"Ána Silva": "Brasil", "Bruno Lima": ""})
        with self.assertRaises(ValueError):
            self.import_people([{"nome": "Ána Silva", "email": "ana@example.org", "pais": "x" * 81}])

    def test_payment_statuses_and_which_ones_release_the_registration(self):
        self.import_people([
            {"nome": "Ána Silva", "email": "ana@example.org", "afiliacao": "A", "pago": "Pago"},
            {"nome": "Bruno Lima", "email": "bruno@example.org", "afiliacao": "B", "pago": "Isento"},
            {"nome": "Carla Dias", "email": "carla@example.org", "afiliacao": "C", "pago": "Boleto pendente"},
            {"nome": "Davi Melo", "email": "davi@example.org", "afiliacao": "D", "pago": 0},
            {"nome": "Eva Reis", "email": "eva@example.org", "afiliacao": "E"}])
        admin = self.login("admin")
        items = self.items(admin)
        self.assertEqual({name: (item["payment_status"], item["paid"]) for name, item in items.items()}, {
            "Ána Silva": ("Pago", True), "Bruno Lima": ("Isento", False), "Carla Dias": ("Boleto pendente", False),
            "Davi Melo": ("Não pago", False), "Eva Reis": ("Não informado", False)})
        code, config, _ = self.request("/api/payment/config", cookie=admin[0])
        self.assertEqual(config["ok"], ["Pago"])
        self.assertEqual([(o["value"], o["total"], o["ok"]) for o in config["options"]], [
            ("Pago", 1, True), ("Boleto pendente", 1, False), ("Isento", 1, False), ("Não informado", 1, False),
            ("Não pago", 1, False)])
        # "Isento" passa a liberar (grafia diferente vale a mesma situação); uma situação nova entra na lista.
        code, saved, _ = self.request("/api/payment/config",
                                      {"ok": ["Pago", "isento"], "options": ["Cortesia"]}, *admin)
        self.assertEqual((code, saved["updated"]), (200, 1))
        self.assertTrue(self.items(admin)["Bruno Lima"]["paid"])
        self.assertIn(("Cortesia", 0, False), [(o["value"], o["total"], o["ok"]) for o in saved["options"]])
        # Quem libera entra na fila de separação; os demais ficam com pendência.
        for email in ("bruno@example.org", "carla@example.org"):
            with database.connect() as db:
                db.execute("UPDATE participants SET status='prechecked', prechecked_at='2026-01-01T00:00:00+00:00' "
                           "WHERE email_key=(SELECT email_key FROM participants WHERE id=?)", (self.pid_by_email(email),))
        queue = self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]
        self.assertEqual([item["name"] for item in queue], ["Bruno Lima"])
        # Pela linha da tabela: situação em texto (ou 0/1, como antes), usando a grafia já conhecida.
        carla = self.pid_by_email("carla@example.org")
        code, result, _ = self.request("/api/participants/payment", {"id": carla, "payment_status": "cortesia"}, *admin)
        self.assertEqual((code, result["item"]["payment_status"], result["item"]["paid"]), (200, "Cortesia", False))
        code, result, _ = self.request("/api/participants/payment", {"id": carla, "paid": 1}, *admin)
        self.assertEqual((code, result["item"]["payment_status"], result["item"]["paid"]), (200, "Pago", True))
        self.assertEqual(self.request("/api/participants/payment", {"id": carla, "payment_status": ""}, *admin)[0], 400)
        self.assertEqual(self.request("/api/payment/config", {"ok": "Pago"}, *admin)[0], 400)
        self.assertEqual(self.request("/api/payment/config", {"ok": ["Pago"]}, *self.login("vol1"))[0], 403)

    def test_batch_changes_apply_the_same_rules_and_report_who_was_skipped(self):
        admin = self.login("admin")
        ana, bruno = self.pid_by_email("ana@example.org"), self.pid_by_email("bruno@example.org")

        def batch(field, value, ids=(ana, bruno)):
            code, result, _ = self.request("/api/participants/batch", {"ids": list(ids), "field": field, "value": value},
                                           *admin)
            self.assertEqual(code, 200)
            return result

        self.assertEqual(batch("priority", "1"), {"updated": 2, "unchanged": 0, "skipped": []})
        self.assertEqual({i["guiche"] for i in self.items(admin).values()}, {"P"})
        self.assertEqual(batch("priority", "1")["unchanged"], 2)
        self.assertEqual(batch("priority", "0")["updated"], 2)
        self.assertEqual(batch("guiche", "3")["updated"], 2)
        self.assertEqual({i["guiche"] for i in self.items(admin).values()}, {"3"})
        # Ana sem pagamento: não pode ir para "Em busca"; Bruno vai.
        self.assertEqual(batch("payment", "Não pago", [ana])["updated"], 1)
        result = batch("status", "searching")
        self.assertEqual((result["updated"], [s["name"] for s in result["skipped"]]), (1, ["Ána Silva"]))
        self.assertEqual(result["skipped"][0]["reason"],
                         "Pendência: pagamento “Não pago” não libera o credenciamento. Resolva antes de avançar a situação.")
        # Com a busca começada, o guichê não muda mais.
        result = batch("guiche", "1")
        self.assertEqual((result["updated"], [s["name"] for s in result["skipped"]]), (1, ["Bruno Lima"]))
        self.assertEqual(batch("payment", "Pago")["updated"], 1)
        self.assertEqual(batch("status", "completed")["updated"], 2)
        with database.connect() as db:
            actions = [r[0] for r in db.execute("SELECT action FROM events WHERE participant_id=? ORDER BY rowid",
                                                (bruno,))]
        self.assertEqual(actions[-1], "status_completed")
        # Pedidos inválidos.
        for body in ({"ids": [ana], "field": "nome", "value": "x"}, {"ids": [], "field": "status", "value": "ready"},
                     {"ids": [ana], "field": "status", "value": ""}, {"ids": "x", "field": "status", "value": "ready"}):
            self.assertEqual(self.request("/api/participants/batch", body, *admin)[0], 400)
        self.assertEqual(batch("status", "nada")["skipped"][0]["reason"], "Situação inválida.")
        self.assertEqual(batch("guiche", "99")["skipped"][0]["reason"], "Guichê inválido.")
        self.assertEqual(batch("status", "ready", [ana, "nao-existe"])["skipped"][-1]["reason"], "Participante não encontrado.")
        self.assertEqual(self.request("/api/participants/batch", {"ids": [ana], "field": "status", "value": "ready"},
                                      *self.login("vol1"))[0], 403)


if __name__ == "__main__":
    unittest.main()
