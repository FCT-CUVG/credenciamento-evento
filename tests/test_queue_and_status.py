"""Separação, guichês e troca de situação."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from credenciamento import csv_io, desks, participants, settings
from credenciamento import db as database
from support import CredenciamentoTestCase


class QueueAndStatusTest(CredenciamentoTestCase):
    def test_complete_flow_and_atomic_claim(self):
        code, done, _ = self.request("/api/checkin", {"cpf": "12345678901"})
        self.assertEqual((code, done), (200, {"ok": True, "name": "Á*a S***a", "needs_guidance": False, "guiche": "1", "guiche_ranges": [{"from": "A", "to": "D"}], "guiche_priority": False}))
        users = [self.login("vol1"), self.login("vol2")]
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]

        def claim(credentials):
            return self.request("/api/action/claim", {"id": pid}, *credentials)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, users))
        self.assertEqual(sorted(results), [200, 409])
        with database.connect() as db:
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
        self.assertEqual(len(settings.CSV.read_text(encoding="utf-8").splitlines()), 3)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE participant_id=?", (pid,)).fetchone()[0], 5)

    def test_attendant_can_view_other_desks_but_only_complete_own(self):
        input_file = Path(self.temp.name) / "extra-desk.json"
        input_file.write_text(json.dumps([
            {"nome": "Bruno Lima", "nome_cracha": "Bruno", "afiliacao": "Instituto B",
             "email": "bruno@example.org", "cpf": "987.654.321-00", "pago": 1, "guiche": "7"}
        ]), encoding="utf-8")
        csv_io.import_file(input_file)
        attendant = self.login("att1")
        code, desks, _ = self.request("/api/guiches", cookie=attendant[0])
        self.assertEqual(code, 200)
        self.assertIn({"id": "7", "ranges": [], "priority": False}, desks["guiches"])
        self.assertEqual(self.request("/api/checkin", {"cpf": "98765432100"})[0], 200)
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='bruno lima'").fetchone()[0]
        volunteer = self.login("vol1")
        self.assertEqual(self.request("/api/action/claim", {"id": pid}, *volunteer)[0], 200)
        self.assertEqual(self.request("/api/action/ready", {"id": pid}, *volunteer)[0], 200)
        code, queue, _ = self.request("/api/queue?guiche=7", cookie=attendant[0])
        self.assertEqual((code, [item["id"] for item in queue["items"]]), (200, [pid]))
        self.assertEqual(self.request("/api/action/complete", {"id": pid}, *attendant)[0], 409)
        self.assertEqual(self.request("/api/action/undo_ready", {"id": pid}, *attendant)[0], 409)

    def test_volunteer_can_confirm_pickup_at_guiche(self):
        self.assertEqual(self.request("/api/checkin", {"cpf": "12345678901"})[0], 200)
        with database.connect() as db:
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
        with database.connect() as db:
            row = db.execute("SELECT status,claimed_by,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
            actions = [event[0] for event in db.execute("SELECT action FROM events WHERE participant_id=? ORDER BY occurred_at", (pid,))]
        self.assertEqual(tuple(row), ("searching", "vol1", None, None))
        self.assertIn("undo_complete", actions)
        self.assertIn("undo_ready", actions)

    def test_admin_can_adjust_participant_status_and_timestamps(self):
        with database.connect() as db:
            pid = db.execute("SELECT id FROM participants WHERE name_key='ana silva'").fetchone()[0]
        self.assertEqual(self.request("/api/participants/status", {"id": pid, "status": "completed"})[0], 401)
        self.assertEqual(self.request("/api/participants/status", {"id": pid, "status": "completed"}, *self.login("vol1"))[0], 403)
        code, result, _ = self.request("/api/participants/status", {"id": pid, "status": "completed"}, *self.login("admin"))
        self.assertEqual((code, result["item"]["status"]), (200, "completed"))
        with database.connect() as db:
            row = db.execute("SELECT prechecked_at,claimed_at,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
        self.assertTrue(all(row))
        code, result, _ = self.request("/api/participants/status", {"id": pid, "status": "registered"}, *self.login("admin"))
        self.assertEqual((code, result["item"]["status"]), (200, "registered"))
        with database.connect() as db:
            row = db.execute("SELECT prechecked_at,claimed_at,ready_at,completed_at FROM participants WHERE id=?", (pid,)).fetchone()
            actions = [event[0] for event in db.execute("SELECT action FROM events WHERE participant_id=? ORDER BY occurred_at", (pid,))]
        self.assertEqual(tuple(row), (None, None, None, None))
        self.assertIn("status_completed", actions)
        self.assertIn("status_registered", actions)

    def test_status_fields_fill_previous_steps_and_clear_later_ones(self):
        row = {"prechecked_at": "t-arrival", "claimed_at": None, "ready_at": "t-ready",
               "completed_at": None, "claimed_by": "vol2"}
        with patch.object(participants, "now", return_value="agora"):
            self.assertEqual(participants.status_fields(row, "registered", "admin"),
                             {"status": "registered", "prechecked_at": None, "claimed_at": None,
                              "ready_at": None, "completed_at": None, "claimed_by": None})
            self.assertEqual(participants.status_fields(row, "searching", "admin"),
                             {"status": "searching", "prechecked_at": "t-arrival", "claimed_at": "agora",
                              "ready_at": None, "completed_at": None, "claimed_by": "admin"})
            self.assertEqual(participants.status_fields(row, "completed", "admin"),
                             {"status": "completed", "prechecked_at": "t-arrival", "claimed_at": "agora",
                              "ready_at": "t-ready", "completed_at": "agora", "claimed_by": "vol2"})


if __name__ == "__main__":
    unittest.main()
