"""Consolidação da lista original de inscrição, feita no navegador (static/js/dashboard/consolidation-core.js).

O servidor entrega os participantes sem chaves de busca e compara só CPF e e-mail com as chaves; o
cruzamento roda no Node, quando instalado."""
import base64
import csv
import io
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from credenciamento import csv_io
from credenciamento import db as database
from support import CredenciamentoTestCase

CORE = Path(__file__).resolve().parent.parent / "static" / "js" / "dashboard" / "consolidation-core.js"
NODE = shutil.which("node")
RUNNER = f"""
import {{consolidate, decode, keyEntries}} from {json.dumps(CORE.as_uri())};
let input = '';
for await (const chunk of process.stdin) input += chunk;
const {{content, people, manual, matches, movement}} = JSON.parse(input);
const text = decode(Buffer.from(content, 'base64'));
const keyMatches = matches && new Map(matches.map(match => [match.line, match]));
process.stdout.write(JSON.stringify(people ? consolidate(text, people, new Map(manual), keyMatches, movement)
  : keyEntries(text)));
"""

# Exportação do sistema de inscrição salva pelo Excel: Windows-1252, cabeçalhos próprios, CPF como
# número sem o zero à esquerda, datas em dois formatos e um campo com aspas, separador e quebra de
# linha. As linhas contam como no Excel: a quebra dentro do campo não abre outra linha; a vazia, sim.
# A Eva vale pela linha 6 (cancelada, mas credenciada: conta). Fulano (fora do sistema) e o Gil do CPF 666
# (no sistema, não credenciado) têm a última linha cancelada: ficam fora das contas. A linha 13 é a Ana
# com outro nome e e-mail, mas o mesmo CPF da linha 2.
ORIGINAL = ("Id;Nome;Status;E-mail;CPF;Última movimentação;Obs\n"
            '1;Ána Silva;Pago;ana@example.org;123.456.789-01;18/10/2026 10:00;"diz ""oi""; e\nquebra"\n'
            "2;Bruno Lima Júnior;Pago;outro@example.org;98765432100;18/10/2026 10:00;\n"
            "3;Carla Cancelada;CANCELADO;carla@example.org;11111111111;18/10/2026 10:00;\n"
            "4;Eva Duas;Pago;eva@example.org;3333333333;15/10/2026 09:00;\n"
            "5;Eva Duas;Cancelado;eva@example.org;3333333333;20/10/2026 08:30;\n"
            "6;Eva Duas;Pago;eva@example.org;3333333333;2026-10-17;\n"
            "7;Fulano;Pago;fulano@example.org;;;\n"
            "8;Fulano;Cancelado;fulano@example.org;;19/10/2026;\n"
            "\n"
            "10;Gil Souza;Pago;gil@example.org;55555555555;;=1+1\n"
            "11;Gil Souza;Cancelado;gil2@example.org;66666666666;;\n"
            "12;Ana S. Pereira;Cancelado;ana.velho@example.org;123.456.789-01;01/10/2026;\n"
            "Total: 12;;;;;;\n")


@unittest.skipUnless(NODE, "Node não instalado")
class ConsolidationTest(CredenciamentoTestCase):
    def setUp(self):
        super().setUp()
        csv_io.import_text(json.dumps([{"nome": name, "email": email, "cpf": cpf, "pago": 1} for name, email, cpf in (
            ("Carla Cancelada", "carla@example.org", "11111111111"),
            ("Davi Fora", "davi@example.org", "22222222222"),
            ("Eva Duas", "eva@example.org", "03333333333"),
            ("Gil Souza", "gil@example.org", "55555555555"),
            ("Gil Souza", "gil2@example.org", "66666666666"))]), ".json")
        with database.connect() as db:
            for email, actor in (("bruno@example.org", "att1"), ("carla@example.org", "att2"),
                                 ("davi@example.org", "att2"), ("eva@example.org", "att2")):
                database.update_participant(db, self.pid_by_email(email), "complete", actor, status="completed",
                                            claimed_by="vol1", completed_at="2026-10-23T20:00:00+00:00")
            db.commit()
        self.admin = self.login("admin")

    def people(self):
        code, body, _ = self.request("/api/consolidation/people", cookie=self.admin[0])
        self.assertEqual(code, 200)
        return body["people"]

    def node(self, **payload):
        payload["content"] = base64.b64encode(ORIGINAL.encode("cp1252")).decode()
        result = subprocess.run([NODE, "--input-type=module", "-e", RUNNER], input=json.dumps(payload),
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def run_core(self, manual=(), matches=None, **movement):
        """Sem matches: só pelo nome e início do CPF. Com matches: o que o servidor devolveu.
        movement=None: sem coluna de data (vale a ordem do arquivo)."""
        return self.node(people=self.people(), manual=list(manual), matches=matches, **movement)

    def match(self, entries, user="admin"):
        cookie, csrf = self.admin if user == "admin" else self.login(user)
        return self.request("/api/consolidation/match", {"entries": entries}, cookie=cookie, csrf=csrf)

    @staticmethod
    def rows(text):
        return list(csv.DictReader(io.StringIO(text), delimiter=";"))

    def test_people_come_without_lookup_keys_and_only_for_the_coordination(self):
        people = self.people()
        self.assertEqual(len(people), 7)
        bruno = next(p for p in people if p["name"] == "Bruno Lima")
        self.assertEqual((bruno["delivered_by"], bruno["cpf_prefix"], bruno["status"]), ("att1", "987", "completed"))
        self.assertFalse({"email_key", "cpf_key"} & set(bruno))
        self.assertNotIn("lookup", json.dumps(people))
        code, _, _ = self.request("/api/consolidation/people", cookie=self.login("att1")[0])
        self.assertEqual(code, 401)

    def test_each_person_counts_by_the_row_of_the_latest_movement(self):
        result = self.run_core()
        rows = self.rows(result["listCsv"])
        # Every row stays in the file, cancelled ones included; the original columns come back untouched.
        self.assertEqual([row["Id"] for row in rows], ["1", "2", "3", "4", "5", "6", "7", "8", "10", "11", "12"])
        self.assertEqual(list(rows[0])[:7], ["Id", "Nome", "Status", "E-mail", "CPF", "Última movimentação", "Obs"])
        self.assertEqual(rows[0]["Obs"], 'diz "oi"; e\nquebra')
        self.assertEqual((rows[0]["credenciado"], rows[0]["encontrado_por"]), ("não", "nome+cpf_inicio"))
        # Carla is cancelled in the list but was credentialed: the registration was right, she counts.
        self.assertEqual((rows[2]["credenciado"], rows[2]["linha_mais_recente"]), ("sim", ""))
        # Eva: three rows; the latest movement is line 6 (cancelled, but credentialed), the others point to it.
        self.assertEqual([(r["credenciado"], r["credenciado_por"], r["linha_mais_recente"]) for r in rows[3:6]],
                         [("sim", "att2", "6"), ("sim", "att2", ""), ("sim", "att2", "6")])
        # Fulano's latest movement (line 9) is cancelled and he is not in the system: left out.
        self.assertEqual([(r["credenciado"], r["linha_mais_recente"]) for r in rows[6:8]],
                         [("cancelada", "9"), ("cancelada", "")])
        # Homonyms: the CPF start picks the right Gil; formulas are neutralised.
        gil = next(p for p in self.people() if p["cpf_prefix"] == "555")
        self.assertEqual((rows[8]["participante_id"], rows[8]["Obs"]), (gil["id"], "'=1+1"))
        # The other Gil is in the system, not credentialed, cancelled: left out, and not "outside the list".
        self.assertEqual(rows[9]["credenciado"], "cancelada")
        # Line 13 has another name and e-mail but Ana's CPF: same person, counted by line 2.
        self.assertEqual((rows[10]["encontrado_por"], rows[10]["linha_mais_recente"], rows[10]["credenciado"]),
                         ("outra_linha", "2", "não"))

        issues = [(i["ocorrencia"], i["nome"], i["linha_lista"]) for i in result["issues"]]
        self.assertEqual(issues, [("fora_da_lista", "Bruno Lima", ""), ("fora_da_lista", "Davi Fora", ""),
                                  ("nao_credenciado", "Ána Silva", "2"), ("nao_credenciado", "Gil Souza", "11"),
                                  ("nao_encontrado", "Bruno Lima Júnior", "3")])
        not_found = {i["nome"]: [p["name"] for p in i["sugestoes"]] for i in result["issues"]
                     if i["ocorrencia"] == "nao_encontrado"}
        self.assertEqual(not_found, {"Bruno Lima Júnior": ["Bruno Lima"]})
        self.assertEqual([row["ocorrencia"] for row in self.rows(result["issuesCsv"])],
                         [kind for kind, _, _ in issues])
        summary = result["summary"]
        self.assertEqual(summary["list"], {"rows": 11, "people": 5, "yes": 2, "no": 2, "notFound": 1, "cancelled": 2})
        self.assertEqual((summary["dateColumn"], summary["undated"]), ("Última movimentação", 3))
        self.assertEqual(summary["others"], {"total": 2, "completed": 2, "noCpf": 0})
        self.assertEqual(summary["system"], {"total": 7, "arrived": 4, "completed": 4})
        # Everyone in the system without a row in the list goes to the second list.
        others = [(row["nome"], row["cpf_inicio"], row["credenciado"]) for row in self.rows(result["othersCsv"])]
        self.assertEqual(others, [("Bruno Lima", "987", "sim"), ("Davi Fora", "222", "sim")])
        # Only the credentialed, on the row of their latest movement (with and without our columns).
        self.assertEqual([row["Id"] for row in self.rows(result["completedCsv"])], ["3", "5"])
        original = self.rows(result["completedOriginalCsv"])
        self.assertEqual(list(original[0]), ["Id", "Nome", "Status", "E-mail", "CPF", "Última movimentação", "Obs"])
        self.assertEqual([row["Id"] for row in original], ["3", "5"])
        # The export footer (no name) is not an inscription.
        self.assertEqual(summary["ignored"], [{"line": 14, "text": "Total: 12"}])

    def test_without_a_date_column_the_last_row_counts(self):
        result = self.run_core(movement=None)
        rows = self.rows(result["listCsv"])
        self.assertEqual([r["linha_mais_recente"] for r in rows[3:6]], ["7", "7", ""])
        self.assertEqual(result["summary"]["dateColumn"], None)
        self.assertEqual([row["Id"] for row in self.rows(result["completedCsv"])], ["3", "6"])

    def test_confirmed_match_replaces_the_suggestion(self):
        bruno = next(p for p in self.people() if p["name"] == "Bruno Lima")
        result = self.run_core(manual=[[3, bruno["id"]]])
        row = self.rows(result["listCsv"])[1]
        self.assertEqual((row["credenciado"], row["encontrado_por"], row["participante_id"]),
                         ("sim", "manual", bruno["id"]))
        kinds = [(i["ocorrencia"], i["nome"]) for i in result["issues"]]
        self.assertNotIn(("fora_da_lista", "Bruno Lima"), kinds)
        self.assertNotIn(("nao_encontrado", "Bruno Lima Júnior"), kinds)
        self.assertEqual(result["summary"]["manual"], 1)

    def test_only_line_cpf_and_email_go_to_the_server_and_ids_come_back(self):
        entries = self.node()
        # Name and the other columns stay in the browser; the CPF that lost its leading zero is fixed.
        self.assertEqual(sorted({key for entry in entries for key in entry}), ["cpf", "email", "line"])
        self.assertIn({"line": 6, "cpf": "03333333333", "email": "eva@example.org"}, entries)
        self.assertEqual(len(entries), 11)  # every row has CPF or e-mail
        code, body, _ = self.match(entries)
        self.assertEqual(code, 200)
        bruno, eva = self.pid_by_email("bruno@example.org"), self.pid_by_email("eva@example.org")
        found = {match["line"]: match for match in body["matches"]}
        self.assertEqual((found[3]["cpf"], found[3]["email"]), (bruno, []))
        self.assertEqual((found[6]["cpf"], found[6]["email"]), (eva, [eva]))
        self.assertNotIn(8, found)  # Fulano is not in the system
        self.assertNotIn("_key", json.dumps(body))
        code, _, _ = self.match(entries, user="att1")
        self.assertEqual(code, 403)
        code, _, _ = self.match([{"cpf": "1"}])
        self.assertEqual(code, 400)

    def test_cpf_and_email_find_who_changed_name(self):
        _, body, _ = self.match(self.node())
        result = self.run_core(matches=body["matches"])
        rows = self.rows(result["listCsv"])
        bruno = self.pid_by_email("bruno@example.org")
        # Bruno changed his name and e-mail: found by the CPF, no longer a pending match.
        self.assertEqual((rows[1]["credenciado"], rows[1]["encontrado_por"], rows[1]["participante_id"]),
                         ("sim", "cpf", bruno))
        self.assertEqual((rows[0]["encontrado_por"], rows[2]["encontrado_por"]), ("nome+email", "nome+email"))
        kinds = [(i["ocorrencia"], i["nome"]) for i in result["issues"]]
        self.assertNotIn(("nao_encontrado", "Bruno Lima Júnior"), kinds)
        self.assertNotIn(("fora_da_lista", "Bruno Lima"), kinds)
        self.assertFalse([kind for kind in kinds if kind[1] == "Fulano"])  # cancelled, not in the system


if __name__ == "__main__":
    unittest.main()
