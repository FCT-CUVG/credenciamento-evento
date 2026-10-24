"""scripts/credenciados.py: lista original de inscrição + backup do painel -> credenciados e pendências."""
import csv
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from credenciamento import csv_io, settings
from credenciamento import db as database
from support import CredenciamentoTestCase

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "credenciados.py"

# Exportação do sistema de inscrição salva pelo Excel: Windows-1252, cabeçalhos próprios ("Nome",
# "E-mail", "CPF", "Status" de pagamento) e CPF guardado como número, sem o zero à esquerda.
ORIGINAL = ("Id;Nome;Status;Nome para Crachá;E-mail;Receber E-mail;CPF\n"
            "1;Ána Silva;Pago;Ana;ana@example.org;Sim;123.456.789-01\n"
            "2;Bruno Lima Júnior;Pago;Bruno;outro@example.org;Não;98765432100\n"
            "3;Carla Cancelada;CANCELADO;Carla;carla@example.org;Sim;11111111111\n"
            "4;Eva Duas;Cancelado;Eva;eva@example.org;Sim;3333333333\n"
            "5;Eva Duas;Pago;Eva;eva@example.org;Sim;3333333333\n"
            "6;Eva Duas;Pago;Eva;eva@example.org;Sim;3333333333\n"
            "7;Fulano;Pago;Fulano;fulano@example.org;Sim;\n"
            "Total: 7;;;;;;\n")


class CredenciadosScriptTest(CredenciamentoTestCase):
    def setUp(self):
        super().setUp()
        csv_io.import_text(json.dumps([{"nome": name, "email": email, "cpf": cpf, "pago": 1} for name, email, cpf in (
            ("Carla Cancelada", "carla@example.org", "11111111111"),
            ("Davi Fora", "davi@example.org", "22222222222"),
            ("Eva Duas", "eva@example.org", "03333333333"))]), ".json")
        with database.connect() as db:
            for email, actor in (("bruno@example.org", "att1"), ("carla@example.org", "att2"),
                                 ("davi@example.org", "att2"), ("eva@example.org", "att2")):
                database.update_participant(db, self.pid_by_email(email), "complete", actor, status="completed",
                                            claimed_by="vol1", completed_at="2026-10-23T20:00:00+00:00")
            db.commit()
        self.folder = Path(self.temp.name)
        (self.folder / "inscricoes.csv").write_bytes(ORIGINAL.encode("cp1252"))
        admin = self.login("admin")
        code, body, _ = self.request("/api/backup/download", cookie=admin[0], json_response=False)
        self.assertEqual(code, 200)
        (self.folder / "backup.json").write_bytes(body)

    def run_script(self, secret=None):
        env = dict(os.environ, CHECKIN_LOOKUP_SECRET=(secret or settings.LOOKUP_SECRET).decode())
        return subprocess.run([sys.executable, "-I", SCRIPT, self.folder / "inscricoes.csv",
                               self.folder / "backup.json", self.folder / "saida.csv"],
                              capture_output=True, text=True, env=env)

    def read(self, name):
        content = (self.folder / name).read_text(encoding="utf-8-sig")
        return list(csv.DictReader(io.StringIO(content), delimiter=";"))

    def test_cancelled_rows_are_left_out_and_every_issue_is_listed(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.read("saida.csv")
        # Cancelled rows are not in the final list; the original columns come back untouched.
        self.assertEqual([row["Id"] for row in rows], ["1", "2", "5", "6", "7"])
        self.assertEqual(list(rows[0])[:7], ["Id", "Nome", "Status", "Nome para Crachá", "E-mail", "Receber E-mail",
                                              "CPF"])
        self.assertEqual(rows[0]["CPF"], "123.456.789-01")
        self.assertEqual((rows[0]["credenciado"], rows[0]["situacao"]), ("não", "inscrito (não chegou)"))
        # Bruno changed name and e-mail: found by CPF.
        self.assertEqual((rows[1]["credenciado"], rows[1]["credenciado_por"], rows[1]["buscado_por"],
                          rows[1]["encontrado_por"]), ("sim", "att1", "vol1", "cpf"))
        # Eva's two valid rows are the same person: the second one points to the first.
        self.assertEqual((rows[2]["credenciado"], rows[2]["mesma_pessoa_que_linha"]), ("sim", ""))
        self.assertEqual(rows[3]["mesma_pessoa_que_linha"], "6")
        self.assertEqual(rows[2]["linhas_canceladas_mesma_pessoa"], "5")
        self.assertEqual(rows[4]["credenciado"], "não encontrado")

        issues = [(row["ocorrencia"], row["nome"], row["linha_lista"]) for row in self.read("saida-pendencias.csv")]
        self.assertEqual(issues, [("cancelado_e_credenciado", "Carla Cancelada", "4"),
                                  ("fora_da_lista", "Davi Fora", ""),
                                  ("nao_credenciado", "Ána Silva", "2"),
                                  ("nao_encontrado", "Fulano", "8")])
        # The summary counts people, not rows, and repeats the panel totals from the backup.
        self.assertIn("3 pessoas encontradas, 2 credenciadas e 1 não credenciadas", result.stdout)
        self.assertIn("1 linhas repetem a pessoa de outra linha", result.stdout)
        self.assertIn("Linhas sem nome ignoradas (ex.: total ou rodapé da exportação): 9.", result.stdout)
        self.assertIn("5 inscritos; 4 chegaram; 4 credenciados", result.stdout)
        self.assertIn("  - Carla Cancelada (credenciado; linha 4)", result.stdout)
        self.assertIn("  - Davi Fora (credenciado)", result.stdout)

    def test_backup_made_with_another_key_is_refused(self):
        result = self.run_script(b"another-lookup-secret-longer-than-32-characters")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chave errada", result.stderr)
        self.assertFalse((self.folder / "saida.csv").exists())


if __name__ == "__main__":
    unittest.main()
