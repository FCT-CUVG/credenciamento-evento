"""Backup pelo painel: baixar, limpar e restaurar participantes e histórico."""
import json
import unittest

from credenciamento import db as database
from credenciamento import settings
from support import CredenciamentoTestCase


class BackupTest(CredenciamentoTestCase):
    def download(self, admin):
        code, body, headers = self.request("/api/backup/download", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        return json.loads(body)

    def snapshot(self):
        with database.connect() as db:
            people = [tuple(r) for r in db.execute(
                "SELECT id, name, status, guiche, payment_status, paid, prechecked_at, completed_at, claimed_by, revision "
                "FROM participants ORDER BY id")]
            events = [tuple(r) for r in db.execute("SELECT id, participant_id, action, actor, occurred_at FROM events "
                                                   "ORDER BY id")]
        return people, events

    def test_backup_clear_and_restore_keep_who_was_registered_and_the_history(self):
        admin, volunteer = self.login("admin"), self.login("vol1")
        ana = self.pid_by_email("ana@example.org")
        self.request("/api/checkin", {"cpf": "12345678901"})
        for action in ("claim", "ready", "complete"):
            self.assertEqual(self.request("/api/action/" + action, {"id": ana}, *volunteer)[0], 200)
        before = self.snapshot()
        backup = self.download(admin)
        self.assertEqual((len(backup["participants"]), len(backup["events"])), (2, len(before[1])))
        self.assertFalse({"cpf", "email"} & set(backup["participants"][0]))

        # Limpar: exige a palavra, apaga participantes e histórico, mantém contas e guarda cópia no servidor.
        self.assertEqual(self.request("/api/participants/clear", {"confirm": "sim"}, *admin)[0], 400)
        self.assertEqual(self.request("/api/participants/clear", {"confirm": "LIMPAR"}, *volunteer)[0], 403)
        code, result, _ = self.request("/api/participants/clear", {"confirm": "LIMPAR"}, *admin)
        self.assertEqual((code, result["participants"], result["events"]), (200, 2, len(before[1])))
        self.assertTrue((settings.DATA / "backups-painel" / result["server_copy"]).exists())
        self.assertEqual(self.snapshot(), ([], []))
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sheet_outbox").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 4)
        self.assertEqual(self.request("/api/checkin", {"cpf": "12345678901"})[0], 404)

        # Restaurar devolve exatamente quem estava credenciado, os horários e o histórico.
        self.assertEqual(self.request("/api/backup/restore", {"confirm": "x", "backup": backup}, *admin)[0], 400)
        code, result, _ = self.request("/api/backup/restore", {"confirm": "RESTAURAR", "backup": backup}, *admin)
        self.assertEqual((code, result["participants"], result["events"]), (200, 2, len(before[1])))
        self.assertEqual(self.snapshot(), before)
        # A busca pública volta a reconhecer as pessoas (mesma chave de busca).
        code, again, _ = self.request("/api/checkin", {"cpf": "12345678901"})
        self.assertEqual((code, again.get("completed")), (409, True))
        self.assertEqual(self.request("/api/checkin", {"cpf": "98765432100"})[0], 200)

    def test_restore_rejects_other_files_and_keeps_current_data(self):
        admin = self.login("admin")
        backup = self.download(admin)
        before = self.snapshot()
        broken = [
            {"format": "outro"},
            dict(backup, version=2),
            dict(backup, lookup="0" * 16),
            dict(backup, participants=[dict(backup["participants"][0], status="voando")]),
            dict(backup, participants=backup["participants"] * 2),
            dict(backup, events=[{"id": "e", "participant_id": "nao-existe", "action": "x", "actor": "y",
                                  "occurred_at": "z"}]),
        ]
        for item in broken:
            code, result, _ = self.request("/api/backup/restore", {"confirm": "RESTAURAR", "backup": item}, *admin)
            self.assertEqual(code, 400, item.get("format"))
            self.assertEqual(self.snapshot(), before)
        code, result, _ = self.request("/api/backup/restore",
                                       {"confirm": "RESTAURAR", "backup": dict(backup, lookup="0" * 16)}, *admin)
        self.assertIn("outra chave de busca", result["error"])
        self.assertEqual(self.request("/api/backup/download", cookie=self.login("vol1")[0], json_response=False)[0], 401)

    def test_restored_payment_follows_the_current_rule(self):
        admin = self.login("admin")
        backup = self.download(admin)
        self.request("/api/payment/config", {"ok": ["Isento"], "options": []}, *admin)
        self.request("/api/backup/restore", {"confirm": "RESTAURAR", "backup": backup}, *admin)
        with database.connect() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT paid FROM participants")}, {0})


if __name__ == "__main__":
    unittest.main()
