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
# "E-mail", "CPF", "Status" de pagamento, data da última movimentação em dois formatos), CPF guardado
# como número, sem o zero à esquerda, e o total no rodapé. Zeca só tem inscrição cancelada e não está no
# sistema (fica fora das contas); as linhas 10 e 11 são a Ana e o Bruno com outros dados, mas o mesmo
# CPF ou e-mail de outra linha.
ORIGINAL = ("Id;Nome;Status;Nome para Crachá;E-mail;Receber E-mail;CPF;Data da última movimentação\n"
            "1;Ána Silva;Pago;Ana;ana@example.org;Sim;123.456.789-01;18/10/2026\n"
            "2;Bruno Lima Júnior;Pago;Bruno;outro@example.org;Não;98765432100;18/10/2026\n"
            "3;Carla Cancelada;CANCELADO;Carla;carla@example.org;Sim;11111111111;18/10/2026\n"
            "4;Eva Duas;Pago;Eva;eva@example.org;Sim;3333333333;15/10/2026 09:00\n"
            "5;Eva Duas;Cancelado;Eva;eva@example.org;Sim;3333333333;20/10/2026 08:30\n"
            "6;Eva Duas;Pago;Eva;eva@example.org;Sim;3333333333;2026-10-17\n"
            "7;Fulano;Pago;Fulano;fulano@example.org;Sim;;\n"
            "8;Zeca Desistente;Cancelado;Zeca;zeca@example.org;Sim;77777777777;19/10/2026\n"
            "9;Ana S. Pereira;Cancelado;Ana;ana.velho@example.org;Sim;123.456.789-01;01/10/2026\n"
            "10;Bruno L. Jr;Cancelado;Bruno;outro@example.org;Não;;05/10/2026\n"
            "Total: 10;;;;;;;\n")


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

    def test_each_person_counts_by_the_row_of_the_latest_movement(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.read("saida.csv")
        # Every row stays, whatever the status; the footer is not an inscription.
        self.assertEqual([row["Id"] for row in rows], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"])
        self.assertEqual(list(rows[0])[:8], ["Id", "Nome", "Status", "Nome para Crachá", "E-mail", "Receber E-mail",
                                              "CPF", "Data da última movimentação"])
        self.assertEqual(rows[0]["CPF"], "123.456.789-01")
        self.assertEqual((rows[0]["credenciado"], rows[0]["situacao"]), ("não", "inscrito (não chegou)"))
        # Bruno changed name and e-mail: found by CPF.
        self.assertEqual((rows[1]["credenciado"], rows[1]["credenciado_por"], rows[1]["buscado_por"],
                          rows[1]["encontrado_por"]), ("sim", "att1", "vol1", "cpf"))
        # Carla is cancelled in the list, but credentialed: the status does not matter.
        self.assertEqual(rows[2]["credenciado"], "sim")
        # Eva's three rows are one person, counted by line 6 (latest movement, though cancelled).
        self.assertEqual([row["linha_mais_recente"] for row in rows[3:6]], ["6", "", "6"])
        self.assertEqual(rows[6]["credenciado"], "não encontrado")
        # Zeca cancelled and was never credentialed: left out of the counts and of the issues.
        self.assertEqual(rows[7]["credenciado"], "cancelada")
        # Same CPF (Ana) or same e-mail (Bruno) as another row: same person, counted by that row.
        self.assertEqual([(r["encontrado_por"], r["linha_mais_recente"]) for r in rows[8:10]],
                         [("cpf", "2"), ("outra_linha", "3")])

        issues = [(row["ocorrencia"], row["nome"], row["linha_lista"]) for row in self.read("saida-pendencias.csv")]
        self.assertEqual(issues, [("fora_da_lista", "Davi Fora", ""),
                                  ("nao_credenciado", "Ána Silva", "2"),
                                  ("nao_encontrado", "Fulano", "8")])
        # The summary counts people, not rows, and repeats the panel totals from the backup.
        self.assertIn("10 linhas, 5 pessoas; vale a linha da última movimentação (coluna Data da última movimentação)",
                      result.stdout)
        self.assertIn("Inscrições canceladas fora das contas (quem foi credenciado conta, mesmo cancelado): 1.",
                      result.stdout)
        self.assertIn("4 encontradas, 3 credenciadas e 1 não credenciadas; 1 não encontradas", result.stdout)
        self.assertIn("5 inscritos; 4 chegaram; 4 credenciados", result.stdout)
        self.assertIn("  - Davi Fora (credenciado)", result.stdout)
        self.assertNotIn("ignorada", result.stdout)

    def test_backup_made_with_another_key_is_refused(self):
        result = self.run_script(b"another-lookup-secret-longer-than-32-characters")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chave errada", result.stderr)
        self.assertFalse((self.folder / "saida.csv").exists())


if __name__ == "__main__":
    unittest.main()
