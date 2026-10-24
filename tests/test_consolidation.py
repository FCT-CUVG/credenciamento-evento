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
const {{content, people, manual, matches}} = JSON.parse(input);
const text = decode(Buffer.from(content, 'base64'));
const keyMatches = matches && new Map(matches.map(match => [match.line, match]));
process.stdout.write(JSON.stringify(people ? consolidate(text, people, new Map(manual), keyMatches)
  : keyEntries(text)));
"""

# Exportação do sistema de inscrição salva pelo Excel: Windows-1252, cabeçalhos próprios, CPF como
# número sem o zero à esquerda e um campo com aspas, separador e quebra de linha. As linhas contam
# como no Excel: a quebra dentro do campo não abre outra linha; a linha vazia, sim.
ORIGINAL = ("Id;Nome;Status;E-mail;CPF;Obs\n"
            '1;Ána Silva;Pago;ana@example.org;123.456.789-01;"diz ""oi""; e\nquebra"\n'
            "2;Bruno Lima Júnior;Pago;outro@example.org;98765432100;\n"
            "3;Carla Cancelada;CANCELADO;carla@example.org;11111111111;\n"
            "4;Eva Duas;Cancelado;eva@example.org;3333333333;\n"
            "5;Eva Duas;Pago;eva@example.org;3333333333;\n"
            "6;Eva Duas;Pago;eva@example.org;3333333333;\n"
            "7;Fulano;Pago;fulano@example.org;;\n"
            "\n"
            "9;Gil Souza;Pago;gil@example.org;55555555555;=1+1\n"
            "Total: 9;;;;;\n")


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

    def run_core(self, manual=(), matches=None):
        """Sem matches: só pelo nome e início do CPF. Com matches: o que o servidor devolveu."""
        return self.node(people=self.people(), manual=list(manual), matches=matches)

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

    def test_list_is_matched_by_name_and_cpf_start(self):
        result = self.run_core()
        rows = self.rows(result["listCsv"])
        # Cancelled rows are left out; the original columns come back untouched (quoted field included).
        self.assertEqual([row["Id"] for row in rows], ["1", "2", "5", "6", "7", "9"])
        self.assertEqual(list(rows[0])[:6], ["Id", "Nome", "Status", "E-mail", "CPF", "Obs"])
        self.assertEqual(rows[0]["Obs"], 'diz "oi"; e\nquebra')
        self.assertEqual((rows[0]["credenciado"], rows[0]["encontrado_por"]), ("não", "nome+cpf_inicio"))
        # Bruno changed his name in the list: not found, with the system's Bruno as suggestion.
        self.assertEqual(rows[1]["credenciado"], "não encontrado")
        # Eva's CPF lost the leading zero; her two valid rows are the same person.
        self.assertEqual((rows[2]["credenciado"], rows[2]["credenciado_por"], rows[2]["buscado_por"]),
                         ("sim", "att2", "vol1"))
        self.assertEqual(rows[3]["mesma_pessoa_que_linha"], "6")
        # Eva also has a cancelled registration (line 5): she stays in the list, with the line noted.
        self.assertEqual((rows[2]["linhas_canceladas_mesma_pessoa"], rows[3]["linhas_canceladas_mesma_pessoa"]),
                         ("5", "5"))
        self.assertEqual(rows[0]["linhas_canceladas_mesma_pessoa"], "")
        # Homonyms: the CPF start picks the right Gil; formulas are neutralised.
        gil = next(p for p in self.people() if p["cpf_prefix"] == "555")
        self.assertEqual((rows[5]["participante_id"], rows[5]["Obs"]), (gil["id"], "'=1+1"))

        issues = [(i["ocorrencia"], i["nome"], i["linha_lista"]) for i in result["issues"]]
        self.assertEqual(issues, [("cancelado_e_credenciado", "Carla Cancelada", "4"),
                                  ("fora_da_lista", "Bruno Lima", ""), ("fora_da_lista", "Davi Fora", ""),
                                  ("fora_da_lista", "Gil Souza", ""),
                                  ("nao_credenciado", "Ána Silva", "2"), ("nao_credenciado", "Gil Souza", "10"),
                                  ("nao_encontrado", "Bruno Lima Júnior", "3"), ("nao_encontrado", "Fulano", "8")])
        not_found = {i["nome"]: [p["name"] for p in i["sugestoes"]] for i in result["issues"]
                     if i["ocorrencia"] == "nao_encontrado"}
        self.assertEqual(not_found, {"Bruno Lima Júnior": ["Bruno Lima"], "Fulano": []})
        self.assertEqual([row["ocorrencia"] for row in self.rows(result["issuesCsv"])],
                         [kind for kind, _, _ in issues])
        summary = result["summary"]
        self.assertEqual((summary["rows"], summary["cancelled"], summary["matched"], summary["matchedCompleted"],
                          summary["repeated"]), (8, 2, 3, 1, 1))
        self.assertEqual(summary["system"], {"total": 7, "arrived": 4, "completed": 4})
        # Counts follow the rows of the downloaded list (Eva is in two rows).
        self.assertEqual(summary["list"], {"rows": 6, "yes": 2, "no": 2, "notFound": 2})
        self.assertEqual(summary["statuses"], [["Pago", 6], ["CANCELADO", 1], ["Cancelado", 1]])
        self.assertEqual(summary["others"], {"total": 4, "completed": 3, "noCpf": 0})
        # Everyone in the system without a valid row goes to the second list, with the reason.
        others = {row["nome"] + "/" + row["cpf_inicio"]: (row["credenciado"], row["motivo"])
                  for row in self.rows(result["othersCsv"])}
        self.assertEqual(others, {"Bruno Lima/987": ("sim", "não está na lista"),
                                  "Carla Cancelada/111": ("sim", "inscrição cancelada na lista (linha 4)"),
                                  "Davi Fora/222": ("sim", "não está na lista"),
                                  "Gil Souza/666": ("não", "não está na lista")})
        # Only the credentialed, one row per person (Eva's second row is left out).
        self.assertEqual([(row["Id"], row["credenciado"]) for row in self.rows(result["completedCsv"])], [("5", "sim")])
        # The export footer (no name) is not an inscription: it is reported, not counted.
        self.assertEqual(summary["ignored"], [{"line": 11, "text": "Total: 9"}])

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
        self.assertIn(("nao_encontrado", "Fulano"), kinds)


if __name__ == "__main__":
    unittest.main()
