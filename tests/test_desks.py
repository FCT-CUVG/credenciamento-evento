"""Faixas de letras, guichê de prioridade e propagação de mudanças de guichê."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from credenciamento import csv_io, desks, settings
from credenciamento import db as database
from credenciamento.bootstrap import init_db
from support import CredenciamentoTestCase


class DesksTest(CredenciamentoTestCase):
    def test_admin_configures_non_overlapping_guiche_ranges(self):
        ranges_file = Path(self.temp.name) / "guiches.json"
        ranges_file.write_text(settings.RANGES.read_text(encoding="utf-8"), encoding="utf-8")
        admin = self.login("admin")
        with patch.object(settings, "RANGES", ranges_file):
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
            self.assertEqual(desks.guiche_for("Daniel"), "2")
            self.assertEqual(desks.guiche_for("Ana"), "1")

    def test_priority_participants_use_priority_desk_and_lead_separation(self):
        admin = self.login("admin")
        csv_io.import_text(json.dumps([
            {"nome": "Zeca Prioritário", "email": "zeca@example.org", "cpf": "11111111111",
             "afiliacao": "Instituto Z", "pago": 1, "prioridade": 1},
            {"nome": "Paula Explícita", "email": "paula@example.org", "afiliacao": "Instituto P",
             "pago": 1, "prioridade": 1, "guiche": "3"}]), ".json")
        with database.connect() as db:
            desks = dict(db.execute("SELECT email, guiche FROM participants").fetchall())
        self.assertEqual((desks["zeca@example.org"], desks["paula@example.org"], desks["ana@example.org"]), ("P", "P", "1"))
        self.assertIn({"id": "P", "ranges": [], "priority": True},
                      self.request("/api/guiches", cookie=admin[0])[1]["guiches"])
        for cpf in ("12345678901", "11111111111"):
            token = self.request("/api/lookup", {"cpf": cpf})[1]["token"]
            code, done, _ = self.request("/api/precheck", {"token": token})
        self.assertEqual((code, done["guiche"], done["guiche_ranges"], done["guiche_priority"]), (200, "P", [], True))
        queue = self.request("/api/queue", cookie=self.login("vol1")[0])[1]["items"]
        self.assertEqual([(item["name"], item["priority"]) for item in queue],
                         [("Zeca Prioritário", True), ("Ána Silva", False)])
        dashboard = self.request("/api/dashboard", cookie=admin[0])[1]
        desks = {d["id"]: (d["total"], d["priority"]) for d in dashboard["desks"]}
        self.assertEqual((desks["1"], desks["P"], dashboard["registration_pending"]), ((2, False), (2, True), 0))
        csv_io.import_text(json.dumps([{"nome": "Beto Sem Afiliação", "email": "beto@example.org", "pago": 1}]), ".json")
        dashboard = self.request("/api/dashboard", cookie=admin[0])[1]
        self.assertEqual((next(d["total"] for d in dashboard["desks"] if d["id"] == "1"), dashboard["registration_pending"]), (3, 1))
        config = self.request("/api/guiches/config", cookie=admin[0])[1]
        self.assertEqual(config["priority_guiche"], "P")
        self.assertEqual(self.request("/api/guiches/config", {"ranges": config["ranges"], "priority_guiche": "P P"}, *admin)[0], 400)
        code, updated, _ = self.request("/api/guiches/config", {"ranges": config["ranges"], "priority_guiche": ""}, *admin)
        self.assertEqual((code, updated["priority_guiche"]), (200, ""))
        csv_io.import_text(json.dumps([{"nome": "Yara Prioritária", "email": "yara@example.org", "pago": 1,
                                     "afiliacao": "Instituto Y", "prioridade": 1}]), ".json")
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT guiche FROM participants WHERE email='yara@example.org'").fetchone()[0], "5")
        code, updated, _ = self.request("/api/guiches/config", {"ranges": config["ranges"]}, *admin)
        self.assertEqual(updated["priority_guiche"], "")

    def test_admin_toggles_priority_and_desk_follows(self):
        admin = self.login("admin")
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE email='bruno@example.org'").fetchone()[0]
        self.assertEqual(self.request("/api/participants/priority", {"id": pid, "priority": 1}, *self.login("vol1"))[0], 403)
        self.assertEqual(self.request("/api/participants/priority", {"id": pid, "priority": 2}, *admin)[0], 400)
        code, updated, _ = self.request("/api/participants/priority", {"id": pid, "priority": 1}, *admin)
        self.assertEqual((code, updated["item"]["priority"], updated["item"]["guiche"]), (200, True, "P"))
        code, updated, _ = self.request("/api/participants/priority", {"id": pid, "priority": 0}, *admin)
        self.assertEqual((code, updated["item"]["priority"], updated["item"]["guiche"]), (200, False, "1"))
        with database.connect() as db:
            actions = [r[0] for r in db.execute("SELECT action FROM events WHERE participant_id=? AND action LIKE 'priority_%' ORDER BY rowid", (pid,))]
        self.assertEqual(actions, ["priority_on", "priority_off"])
        token = self.request("/api/lookup", {"cpf": "98765432100"})[1]["token"]
        self.request("/api/precheck", {"token": token})
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *self.login("vol1"))[0], 200)
        queue_data = self.request("/api/queue", cookie=self.login("vol1")[0])[1]
        self.assertEqual(queue_data["priority_guiche"], "P")
        queue = queue_data["items"]
        self.assertEqual(queue[0]["cpf_prefix"], "987")
        self.assertNotIn("cpf", queue[0])
        self.assertEqual(self.request("/api/participants/priority", {"id": pid, "priority": 1}, *admin)[0], 409)

    def test_saving_desks_updates_participants_and_attendants(self):
        admin = self.login("admin")
        csv_io.import_text(json.dumps([
            {"nome": "Carla Manual", "email": "carla@example.org", "afiliacao": "C", "pago": 1, "guiche": "7"},
            {"nome": "Diego Rocha", "email": "diego@example.org", "afiliacao": "D", "pago": 1},
            {"nome": "Zé Prioridade", "email": "ze@example.org", "afiliacao": "Z", "pago": 1, "prioridade": 1}]), ".json")
        token = self.request("/api/lookup", {"cpf": "12345678901"})[1]["token"]
        self.request("/api/precheck", {"token": token})
        with database.connect() as db:
            ana = db.execute("SELECT id FROM participants WHERE email='ana@example.org'").fetchone()[0]
        self.assertEqual(self.request("/api/action/claim", {"id": ana}, *self.login("vol1"))[0], 200)

        def desks():
            with database.connect() as db:
                people = dict(db.execute("SELECT email, guiche FROM participants").fetchall())
                people["att1"] = db.execute("SELECT guiche FROM users WHERE username='att1'").fetchone()[0]
            return people

        ranges = self.request("/api/guiches/config", cookie=admin[0])[1]["ranges"]
        renamed = [dict(r, guiche="1A") if r["guiche"] == "1" else r for r in ranges]
        code, result, _ = self.request("/api/guiches/config", {"ranges": renamed, "priority_guiche": "P"}, *admin)
        self.assertEqual((code, result["updated"]), (200, 3))
        after = desks()
        self.assertEqual((after["ana@example.org"], after["bruno@example.org"], after["diego@example.org"],
                          after["carla@example.org"], after["ze@example.org"], after["att1"]),
                         ("1A", "1A", "1A", "7", "P", "1A"))
        moved = [dict(r) for r in renamed]
        moved[0]["to"], moved[1]["from"] = "C", "D"
        self.assertEqual(self.request("/api/guiches/config", {"ranges": moved, "priority_guiche": "PRI"}, *admin)[1]["updated"], 2)
        after = desks()
        self.assertEqual((after["ana@example.org"], after["diego@example.org"], after["carla@example.org"], after["ze@example.org"]),
                         ("1A", "2", "7", "PRI"))
        self.request("/api/guiches/config", {"ranges": moved, "priority_guiche": ""}, *admin)
        self.assertEqual(desks()["ze@example.org"], "5")
        with database.connect() as db:
            actions = {r[0] for r in db.execute("SELECT action FROM events WHERE actor='admin'")}
        self.assertTrue({"guiche_rename", "guiche_reassign"} <= actions)
        with database.connect() as db:
            db.execute("UPDATE participants SET guiche='2', guiche_manual=1 WHERE email='ze@example.org'")
            db.execute("UPDATE participants SET priority=1 WHERE email='ze@example.org'")
        self.request("/api/guiches/config", {"ranges": moved, "priority_guiche": "P"}, *admin)
        self.assertEqual(desks()["ze@example.org"], "P")
        exported = csv_io.participants_download().decode("utf-8-sig")
        csv_io.import_text(exported, ".csv")
        with database.connect() as db:
            manual = dict(db.execute("SELECT email, guiche_manual FROM participants").fetchall())
        self.assertEqual((manual["carla@example.org"], manual["diego@example.org"], manual["ana@example.org"]), (1, 0, 0))

    def test_stale_priority_desk_merges_into_current_priority_desk(self):
        csv_io.import_text(json.dumps([
            {"nome": "Zeca Prioridade", "email": "zeca@example.org", "afiliacao": "Z", "pago": 1, "prioridade": 1},
            {"nome": "Yara Prioridade", "email": "yara@example.org", "afiliacao": "Y", "pago": 1, "prioridade": 1},
            {"nome": "Carla Manual", "email": "carla@example.org", "afiliacao": "C", "pago": 1, "guiche": "7"}]), ".json")
        with database.connect() as db:
            # Situação deixada por uma renomeação antiga: prioritários em "Prioridade", configuração em "P".
            db.execute("UPDATE participants SET guiche='Prioridade', guiche_manual=1 WHERE priority=1")
            db.execute("UPDATE participants SET status='completed' WHERE email='yara@example.org'")
            db.execute("UPDATE users SET guiche='Prioridade' WHERE username='att1'")
        init_db()
        with database.connect() as db:
            desks = dict(db.execute("SELECT email, guiche FROM participants").fetchall())
            attendant = db.execute("SELECT guiche FROM users WHERE username='att1'").fetchone()[0]
        self.assertEqual((desks["zeca@example.org"], desks["yara@example.org"], desks["carla@example.org"], attendant),
                         ("P", "P", "7", "P"))
        self.assertNotIn("Prioridade", [d["id"] for d in self.request("/api/guiches", cookie=self.login("admin")[0])[1]["guiches"]])

    def test_balanced_ranges_split_people_evenly_without_splitting_letters(self):
        counts = dict.fromkeys(desks.LETTERS, 0)
        counts.update(A=20, B=5, C=15, M=30, S=10, T=20)
        ranges = desks.balanced_ranges(counts, ["1", "2", "3"])
        self.assertEqual([(r["from"], r["to"], r["total"]) for r in ranges],
                         [("A", "H", 40), ("I", "Q", 30), ("R", "Z", 30)])
        self.assertEqual(desks.validate_ranges(ranges), [{k: r[k] for k in ("from", "to", "guiche")} for r in ranges])
        # Uma letra nunca é dividida: com 26 guichês, cada um fica com uma letra.
        self.assertEqual([r["from"] + r["to"] for r in desks.balanced_ranges(counts, [str(i) for i in range(26)])],
                         [letter * 2 for letter in desks.LETTERS])
        # Sem inscritos, as letras são divididas igualmente.
        empty = desks.balanced_ranges(dict.fromkeys(desks.LETTERS, 0), ["1", "2"])
        self.assertEqual([(r["from"], r["to"]) for r in empty], [("A", "M"), ("N", "Z")])
        with self.assertRaises(ValueError):
            desks.balanced_ranges(counts, [])
        self.assertEqual(desks.balanced_desk_names(4, [{"guiche": "B"}, {"guiche": "A"}, {"guiche": "B"}], "P"),
                         ["B", "A", "1", "2"])
        self.assertEqual(desks.balanced_desk_names(2, [{"guiche": "1"}, {"guiche": "2"}, {"guiche": "3"}], "1"),
                         ["2", "3"])

    def test_admin_balances_desks_by_registered_people(self):
        people = [{"nome": f"{initial} Pessoa {index}", "email": f"{initial.lower()}{index}@example.org",
                   "afiliacao": "X", "pago": 1}
                  for initial, amount in (("Carlos", 3), ("Maria", 5), ("Tiago", 2)) for index in range(amount)]
        people += [{"nome": "Zeca Prioridade", "email": "zeca@example.org", "afiliacao": "Z", "pago": 1, "prioridade": 1},
                   {"nome": "Zilda Manual", "email": "zilda@example.org", "afiliacao": "Z", "pago": 1, "guiche": "9"}]
        csv_io.import_text(json.dumps(people), ".json")
        with database.connect() as db:
            db.execute("UPDATE participants SET status='completed' WHERE email LIKE 'maria%' AND email < 'maria3'")
        admin = self.login("admin")
        config = self.request("/api/guiches/config", cookie=admin[0])[1]
        # Ana e Bruno vêm da base dos testes; prioridade e guichê manual não contam.
        self.assertEqual({k: v for k, v in config["letter_counts"]["all"].items() if v},
                         {"A": 1, "B": 1, "C": 3, "M": 5, "T": 2})
        self.assertEqual(config["letter_counts"]["pending"]["M"], 2)
        self.assertEqual(self.request("/api/guiches/balance", {"count": 2}, *self.login("vol1"))[0], 403)
        for count in ("", "0", "27", "dois"):
            self.assertEqual(self.request("/api/guiches/balance", {"count": count}, *admin)[0], 400)
        code, proposal, _ = self.request("/api/guiches/balance", {"count": 2}, *admin)
        self.assertEqual(code, 200)
        self.assertEqual([(r["from"], r["to"], r["guiche"], r["total"]) for r in proposal["ranges"]],
                         [("A", "L", "1", 5), ("M", "Z", "2", 7)])
        code, proposal, _ = self.request("/api/guiches/balance", {"count": 2, "pending_only": True}, *admin)
        self.assertEqual([r["total"] for r in proposal["ranges"]], [5, 4])
        # A proposta não altera nada até ser salva.
        self.assertEqual(self.request("/api/guiches/config", cookie=admin[0])[1]["ranges"], config["ranges"])
        code, saved, _ = self.request("/api/guiches/config", {"ranges": proposal["ranges"], "priority_guiche": "P"}, *admin)
        self.assertEqual((code, saved["ranges"][0]), (200, {"from": "A", "to": "L", "guiche": "1"}))
        with database.connect() as db:
            placed = dict(db.execute("SELECT email, guiche FROM participants").fetchall())
        self.assertEqual((placed["carlos0@example.org"], placed["maria4@example.org"], placed["maria0@example.org"],
                          placed["zeca@example.org"], placed["zilda@example.org"]), ("1", "2", "3", "P", "9"))


if __name__ == "__main__":
    unittest.main()
