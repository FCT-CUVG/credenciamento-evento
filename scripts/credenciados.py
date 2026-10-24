#!/usr/bin/env python3
"""Lista de credenciados a partir da lista original de inscrição + backup .json do painel.

Uso:
  CHECKIN_LOOKUP_SECRET='a-chave-do-servidor' python3 credenciados.py inscricoes.csv backup.json saida.csv

Gera dois arquivos:
  saida.csv             lista original SEM as inscrições canceladas, com todas as colunas e mais:
                        credenciado, credenciado_em, credenciado_por, buscado_por, guiche, situacao,
                        encontrado_por, participante_id, mesma_pessoa_que_linha
  saida-pendencias.csv  uma linha por ocorrência:
                        - cancelado_e_credenciado: inscrição cancelada na lista, mas a pessoa foi
                          credenciada (e não tem outra inscrição válida na lista)
                        - fora_da_lista: está no sistema de credenciamento, mas não em nenhuma
                          inscrição da lista original
                        - nao_credenciado: inscrição válida cuja pessoa não retirou o kit
                        - nao_encontrado: inscrição válida que não existe no sistema de credenciamento

Só usa a biblioteca padrão do Python.
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import sys
import unicodedata

if len(sys.argv) != 4:
    sys.exit(__doc__)
SECRET = os.environ.get("CHECKIN_LOOKUP_SECRET", "").encode()
if len(SECRET) < 32:
    sys.exit("Defina CHECKIN_LOOKUP_SECRET (a mesma do servidor, com pelo menos 32 caracteres).")
list_path, backup_path, output_path = sys.argv[1:]
issues_path = re.sub(r"(\.csv)?$", "-pendencias.csv", output_path, count=1)

SITUACAO = {"registered": "inscrito (não chegou)", "prechecked": "chegou, aguardando busca",
            "searching": "em busca", "ready": "pronto no guichê", "completed": "credenciado"}


def key(kind, value):
    return hmac.new(SECRET, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").strip())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def safe(value):
    """Evita que a planilha execute o conteúdo da célula como fórmula."""
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value


# 1) Backup do painel
backup = json.load(open(backup_path, encoding="utf-8-sig"))
if not isinstance(backup, dict) or backup.get("format") != "credenciamento-backup":
    sys.exit("O arquivo de backup não é um backup do credenciamento.")
if backup.get("lookup") != hmac.new(SECRET, b"backup-fingerprint", hashlib.sha256).hexdigest()[:16]:
    sys.exit("Chave errada: este backup foi feito com outra CHECKIN_LOOKUP_SECRET.")
people = backup["participants"]
delivered_by = {e["participant_id"]: e["actor"] for e in backup["events"] if e["action"] == "complete"}
by_name_email = {(p["name_key"], p["email_key"]): p for p in people}
by_cpf = {p["cpf_key"]: p for p in people if p.get("cpf_key")}
by_email = {}
for p in people:
    by_email.setdefault(p["email_key"], []).append(p)

# 2) Lista original (UTF-8 ou Windows-1252, separada por vírgula, ponto e vírgula ou tabulação)
raw = open(list_path, "rb").read()
try:
    content = raw.decode("utf-8-sig")
except UnicodeDecodeError:
    content = raw.decode("cp1252")
for delimiter in (";", ",", "\t"):
    reader = csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter)
    reader.fieldnames = fields = [f.strip() for f in reader.fieldnames or []]
    column = {name: next((f for f in fields if re.sub(r"[^a-z0-9]", "", normalize(f)) == name), None)
              for name in ("nome", "email", "cpf", "status")}
    if column["nome"] and column["email"]:
        break
else:
    sys.exit("A lista precisa das colunas Nome e E-mail.")
if not column["status"]:
    print("Aviso: a lista não tem coluna Status; nenhuma inscrição será tratada como cancelada.")


def find(row):
    """Pessoa do credenciamento para uma linha da lista: nome + e-mail, depois CPF, depois e-mail
    (se só uma pessoa o usa). Devolve (pessoa ou None, como foi encontrada)."""
    email = str(row.get(column["email"]) or "").strip()
    cpf = re.sub(r"\D", "", str(row.get(column["cpf"]) or "")) if column["cpf"] else ""
    if 9 <= len(cpf) < 11:
        cpf = cpf.zfill(11)  # o Excel tira os zeros à esquerda
    mail = key("email", email.casefold()) if email else ""
    person = by_name_email.get((normalize(row.get(column["nome"])), mail))
    if person:
        return person, "nome+email"
    if len(cpf) == 11 and key("cpf", cpf) in by_cpf:
        return by_cpf[key("cpf", cpf)], "cpf"
    same_email = by_email.get(mail, [])
    return (same_email[0], "email") if len(same_email) == 1 else (None, "")


# 3) Cruzamento
valid, cancelled = [], []  # (linha da planilha, registro, pessoa, como foi encontrada)
ignored = []  # linhas sem nome (ex.: total ou rodapé da exportação): não são inscrições
for row in reader:
    if not any(str(v or "").strip() for v in row.values()):
        continue
    if not str(row.get(column["nome"]) or "").strip():
        ignored.append(str(reader.line_num))
        continue
    is_cancelled = column["status"] and normalize(row.get(column["status"])).startswith("cancel")
    (cancelled if is_cancelled else valid).append((reader.line_num, row, *find(row)))

first_line = {}  # participante -> primeira linha válida da planilha em que apareceu
extra = ["credenciado", "credenciado_em", "credenciado_por", "buscado_por", "guiche", "situacao",
         "encontrado_por", "participante_id", "mesma_pessoa_que_linha"]
issues = []  # (ocorrência, nome, linha na lista, situação, credenciado em, por, guichê, participante)
with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fields + extra, delimiter=delimiter, extrasaction="ignore")
    writer.writeheader()
    for line, row, person, found_by in valid:
        values = {k: row.get(k, "") for k in fields}
        name = row.get(column["nome"], "")
        if person is None:
            values["credenciado"] = "não encontrado"
            issues.append(("nao_encontrado", name, str(line), "", "", "", "", ""))
        else:
            completed = person["status"] == "completed"
            repeated = person["id"] in first_line
            first_line.setdefault(person["id"], line)
            values.update(credenciado="sim" if completed else "não",
                          credenciado_em=(person.get("completed_at") or "") if completed else "",
                          credenciado_por=delivered_by.get(person["id"], "") if completed else "",
                          buscado_por=person.get("claimed_by") or "", guiche=person["guiche"],
                          situacao=SITUACAO.get(person["status"], person["status"]), encontrado_por=found_by,
                          participante_id=person["id"],
                          mesma_pessoa_que_linha=first_line[person["id"]] if repeated else "")
            if not completed and not repeated:
                issues.append(("nao_credenciado", name, str(line), values["situacao"], "", "",
                               person["guiche"], person["id"]))
        writer.writerow({k: safe(v) for k, v in values.items()})

cancelled_lines = {}  # participante -> linhas canceladas da planilha que caíram nele
for line, row, person, _ in cancelled:
    if person:
        cancelled_lines.setdefault(person["id"], []).append(str(line))
for person in people:
    if person["id"] in first_line:
        continue  # tem inscrição válida na lista
    completed = person["status"] == "completed"
    details = (SITUACAO.get(person["status"], person["status"]),
               (person.get("completed_at") or "") if completed else "",
               delivered_by.get(person["id"], "") if completed else "", person["guiche"], person["id"])
    if person["id"] in cancelled_lines:
        if completed:
            issues.append(("cancelado_e_credenciado", person["name"], ",".join(cancelled_lines[person["id"]]),
                           *details))
    else:
        issues.append(("fora_da_lista", person["name"], "", *details))

order = ("cancelado_e_credenciado", "fora_da_lista", "nao_credenciado", "nao_encontrado")
issues.sort(key=lambda issue: (order.index(issue[0]), normalize(issue[1])))
with open(issues_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f, delimiter=delimiter)
    writer.writerow(["ocorrencia", "nome", "linha_lista", "situacao", "credenciado_em", "credenciado_por",
                     "guiche", "participante_id"])
    writer.writerows([[safe(value) for value in issue] for issue in issues])

# 4) Resumo
count = {kind: sum(issue[0] == kind for issue in issues) for kind in order}
matched = [p for p in people if p["id"] in first_line]
print(f"Lista original: {len(valid) + len(cancelled)} linhas; {len(cancelled)} canceladas (fora da lista final); "
      f"{len(valid)} válidas.")
print(f"Inscrições válidas: {len(matched)} pessoas encontradas, {sum(p['status'] == 'completed' for p in matched)} "
      f"credenciadas e {count['nao_credenciado']} não credenciadas; {count['nao_encontrado']} não encontradas no "
      f"sistema; {len(valid) - count['nao_encontrado'] - len(matched)} linhas repetem a pessoa de outra linha.")
if ignored:
    print(f"Linhas sem nome ignoradas (ex.: total ou rodapé da exportação): {', '.join(ignored)}.")
print(f"Backup (deve bater com o painel): {len(people)} inscritos; "
      f"{sum(p['status'] != 'registered' for p in people)} chegaram; "
      f"{sum(p['status'] == 'completed' for p in people)} credenciados.")
for kind, title in (("cancelado_e_credenciado", "Cancelados na lista, mas credenciados"),
                    ("fora_da_lista", "No credenciamento, mas fora da lista original")):
    print(f"\n{title}: {count[kind]}")
    for issue in issues:
        if issue[0] == kind:
            print(f"  - {issue[1]} ({issue[3]}{'; linha ' + issue[2] if issue[2] else ''})")
print(f"\nNão credenciados: {count['nao_credenciado']}; não encontrados: {count['nao_encontrado']} "
      f"(nomes no arquivo de pendências).")
print(f"Arquivos: {output_path} e {issues_path}")
