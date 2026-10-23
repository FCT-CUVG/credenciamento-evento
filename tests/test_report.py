"""Relatório de credenciados: lista original de volta, com quem retirou o kit, por quem e quando."""
import csv
import io
import json
import unittest
from pathlib import Path

from credenciamento import csv_io, settings
from credenciamento.report import credentialed_report
from support import CredenciamentoTestCase

ORIGINAL = ("nome;email;cpf;telefone\n"
            "Ána Silva;ANA@example.org;123.456.789-01;+55 11 9999\n"
            "Bruno Lima;outro@example.org;98765432100;\n"
            "Carla Souza;carla@example.org;11122233344;\n")


class CredentialedReportTest(CredenciamentoTestCase):
    def run_report(self, backup=None):
        folder = Path(self.temp.name)
        (folder / "lista.csv").write_text(ORIGINAL, encoding="utf-8")
        result = credentialed_report(folder / "lista.csv", folder / "saida.csv", backup)
        content = (folder / "saida.csv").read_text(encoding="utf-8-sig")
        return result, list(csv.DictReader(io.StringIO(content), delimiter=";"))

    def test_original_list_comes_back_with_who_delivered_the_kit(self):
        admin, volunteer, attendant = self.login("admin"), self.login("vol1"), self.login("att1")
        ana = self.pid_by_email("ana@example.org")
        self.request("/api/checkin", {"cpf": "12345678901"})
        for action, user in (("claim", volunteer), ("ready", volunteer), ("complete", attendant)):
            self.assertEqual(self.request("/api/action/" + action, {"id": ana}, *user)[0], 200)
        (result, rows) = self.run_report()
        self.assertEqual(result, (3, 1, 1))
        # Original columns are kept as they were (CPF included), with a formula-like phone neutralized.
        self.assertEqual(rows[0]["cpf"], "123.456.789-01")
        self.assertEqual(rows[0]["telefone"], "'+55 11 9999")
        self.assertEqual((rows[0]["credenciado"], rows[0]["credenciado_por"], rows[0]["buscado_por"],
                          rows[0]["guiche"]), ("sim", "att1", "vol1", "1"))
        self.assertTrue(rows[0]["credenciado_em"])
        # Bruno changed e-mail on the list: found by CPF, not credentialed yet.
        self.assertEqual((rows[1]["credenciado"], rows[1]["situacao"]), ("não", "registered"))
        self.assertEqual(rows[2]["credenciado"], "não encontrado")

        backup = Path(self.temp.name) / "backup.json"
        code, body, _ = self.request("/api/backup/download", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        backup.write_bytes(body)
        self.assertEqual(self.request("/api/participants/clear", {"confirm": "LIMPAR"}, *admin)[0], 200)
        self.assertEqual(self.run_report(backup), (result, rows))

    def test_registration_system_export_with_its_own_headers_and_excel_encoding(self):
        volunteer, attendant = self.login("vol1"), self.login("att1")
        bruno = self.pid_by_email("bruno@example.org")
        self.request("/api/checkin", {"cpf": "98765432100"})
        for action, user in (("claim", volunteer), ("ready", volunteer), ("complete", attendant)):
            self.assertEqual(self.request("/api/action/" + action, {"id": bruno}, *user)[0], 200)
        folder = Path(self.temp.name)
        # Exported by the registration system and saved by Excel: Windows-1252, "Nome"/"E-mail"/"CPF",
        # its own "Status" column, CPF stored as a number (leading zero lost) and a renamed person.
        (folder / "inscricoes.csv").write_bytes(
            ("Id;Nome;Status;Nome para Crachá;E-mail;CPF\n"
             "1;Ána Silva;Pago;Ana;ana@example.org;12345678901\n"
             "2;Bruno Lima Júnior;Pago;Bruno;outro@example.org;98765432100\n").encode("cp1252"))
        result = credentialed_report(folder / "inscricoes.csv", folder / "saida.csv")
        self.assertEqual(result, (2, 1, 0))
        rows = list(csv.DictReader(io.StringIO((folder / "saida.csv").read_text(encoding="utf-8-sig")),
                                   delimiter=";"))
        self.assertEqual(list(rows[0])[:6], ["Id", "Nome", "Status", "Nome para Crachá", "E-mail", "CPF"])
        self.assertEqual((rows[0]["Status"], rows[0]["credenciado"]), ("Pago", "não"))
        self.assertEqual((rows[1]["credenciado"], rows[1]["credenciado_por"]), ("sim", "att1"))

    def test_cpf_that_lost_its_leading_zero_still_matches(self):
        csv_io.import_text(json.dumps([{"nome": "Zeca Zero", "email": "zeca@example.org", "cpf": "01234567890"}]),
                           ".json")
        folder = Path(self.temp.name)
        (folder / "lista.csv").write_text("Nome,E-mail,CPF\nJosé Zero,novo@example.org,1234567890\n", encoding="utf-8")
        self.assertEqual(credentialed_report(folder / "lista.csv", folder / "saida.csv"), (1, 0, 0))

    def test_backup_made_with_another_key_is_refused(self):
        admin = self.login("admin")
        backup = Path(self.temp.name) / "backup.json"
        backup.write_bytes(self.request("/api/backup/download", cookie=admin[0], json_response=False)[1])
        settings.LOOKUP_SECRET = b"another-lookup-secret-longer-than-32-characters"
        with self.assertRaisesRegex(ValueError, "outra chave"):
            self.run_report(backup)
        backup.write_text(json.dumps({"format": "x"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "não é um backup"):
            self.run_report(backup)


if __name__ == "__main__":
    unittest.main()
