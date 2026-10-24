#!/usr/bin/env python3
"""Lista de credenciados a partir da lista original de inscrição + backup .json do painel.

Uso:
  CHECKIN_LOOKUP_SECRET='a-chave-do-servidor' python3 credenciados.py inscricoes.csv backup.json saida.csv

Gera dois arquivos:
  saida.csv             lista original com todas as linhas e colunas e mais:
                        credenciado, credenciado_em, credenciado_por, buscado_por, guiche, situacao,
                        encontrado_por, participante_id, linha_mais_recente
  saida-pendencias.csv  uma linha por pessoa:
                        - fora_da_lista: está no sistema de credenciamento, mas em nenhuma linha da lista
                        - nao_credenciado: está na lista e não retirou o kit
                        - nao_encontrado: está na lista e não existe no sistema de credenciamento

A mesma pessoa (mesmo participante ou, sem ele, mesmo CPF ou e-mail) em várias linhas conta pela
linha da última movimentação: a coluna de data com "última movimentação", "atualização" ou
"alteração" no nome (dd/mm/aaaa ou aaaa-mm-dd, com hora opcional) ou, sem ela, a última linha do
arquivo. Nas outras linhas, linha_mais_recente indica qual vale. Se essa linha tem status cancelado
e a pessoa não foi credenciada, ela fica fora das contas e das pendências (credenciado = cancelada);
credenciada, conta, porque a inscrição estava certa.

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
by_id = {p["id"]: p for p in people}
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


def column_with(*words):
    return next((f for f in fields if any(w in re.sub(r"[^a-z0-9]", "", normalize(f)) for w in words)), None)


movement = (column_with("ultimamovimentacao", "ultimaatualizacao", "ultimaalteracao")
            or column_with("movimentacao", "atualizacao", "atualizadoem", "alteracao", "modificacao"))


def moment(value):
    """Data e hora em dd/mm/aaaa [hh:mm[:ss]] ou aaaa-mm-dd [hh:mm[:ss]], para comparar; None se ilegível."""
    text = str(value or "").strip()
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if match:
        day, month, year, hour, minute, second = match.groups()
        year = 2000 + int(year) if len(year) == 2 else int(year)
        return (year, int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if match:
        return tuple(int(part or 0) for part in match.groups())
    return None


def cpf_of(row):
    cpf = re.sub(r"\D", "", str(row.get(column["cpf"]) or "")) if column["cpf"] else ""
    return cpf.zfill(11) if 9 <= len(cpf) < 11 else cpf  # o Excel tira os zeros à esquerda


def find(row):
    """Pessoa do credenciamento para uma linha da lista: nome + e-mail, depois CPF, depois e-mail
    (se só uma pessoa o usa). Devolve (pessoa ou None, como foi encontrada)."""
    email, cpf = str(row.get(column["email"]) or "").strip(), cpf_of(row)
    mail = key("email", email.casefold()) if email else ""
    person = by_name_email.get((normalize(row.get(column["nome"])), mail))
    if person:
        return person, "nome+email"
    if len(cpf) == 11 and key("cpf", cpf) in by_cpf:
        return by_cpf[key("cpf", cpf)], "cpf"
    same_email = by_email.get(mail, [])
    return (same_email[0], "email") if len(same_email) == 1 else (None, "")


# 3) Cruzamento: linhas da mesma pessoa e a da última movimentação de cada uma
items = []  # (linha da planilha, registro, pessoa, como foi encontrada, grupo, data)
for row in reader:
    if not any(str(v or "").strip() for v in row.values()):
        continue
    if not str(row.get(column["nome"]) or "").strip():
        continue  # linha sem nome (ex.: total ou rodapé da exportação): não é inscrição
    person, found_by = find(row)
    cpf, email = cpf_of(row), str(row.get(column["email"]) or "").strip().lower()
    items.append([reader.line_num, row, person, found_by, None, moment(row.get(movement)) if movement else None,
                  cpf if len(cpf) == 11 else "", email])

# Linha sem pessoa do credenciamento, mas com o CPF ou o e-mail de uma linha que tem: é a mesma pessoa
# (ex.: tentativa de inscrição com e-mail digitado errado e CPF certo).
owners = {}
for item in items:
    if item[2]:
        for value in (("c", item[6]), ("e", item[7])):
            if value[1]:
                owners.setdefault(value, set()).add(item[2]["id"])
for item in items:
    if not item[2]:
        ids = set().union(*(owners.get(value, set()) for value in (("c", item[6]), ("e", item[7])) if value[1]))
        if len(ids) == 1:
            item[2], item[3] = by_id[ids.pop()], "outra_linha"
    item[4] = ("p", item[2]["id"]) if item[2] else ("c", item[6]) if item[6] else ("e", item[7]) if item[7] \
        else ("l", item[0])


def recency(item):
    # Sem data legível, conta como mais antiga; empate, vale a de baixo.
    return (item[5] is not None, item[5] or (), item[0])


latest = {}
for item in items:
    if item[4] not in latest or recency(item) > recency(latest[item[4]]):
        latest[item[4]] = item


def cancelled(item):
    return bool(column["status"]) and normalize(item[1].get(column["status"])).startswith("cancel")


# Cancelada na última movimentação e não credenciada: fica fora das contas e das pendências.
left = {group for group, main in latest.items()
        if cancelled(main) and not (main[2] and main[2]["status"] == "completed")}

extra = ["credenciado", "credenciado_em", "credenciado_por", "buscado_por", "guiche", "situacao",
         "encontrado_por", "participante_id", "linha_mais_recente"]
issues = []  # (ocorrência, nome, linha na lista, situação, credenciado em, por, guichê, participante)
with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fields + extra, delimiter=delimiter, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        line, row, person, found_by, group = item[:5]
        main = latest[group]
        values = {k: row.get(k, "") for k in fields}
        values["linha_mais_recente"] = "" if main is item else main[0]
        name = row.get(column["nome"], "")
        if group in left:
            values.update(credenciado="cancelada", encontrado_por=found_by,
                          participante_id=person["id"] if person else "")
        elif person is None:
            values["credenciado"] = "não encontrado"
            if main is item:
                issues.append(("nao_encontrado", name, str(line), "", "", "", "", ""))
        else:
            completed = person["status"] == "completed"
            values.update(credenciado="sim" if completed else "não",
                          credenciado_em=(person.get("completed_at") or "") if completed else "",
                          credenciado_por=delivered_by.get(person["id"], "") if completed else "",
                          buscado_por=person.get("claimed_by") or "", guiche=person["guiche"],
                          situacao=SITUACAO.get(person["status"], person["status"]), encontrado_por=found_by,
                          participante_id=person["id"])
            if not completed and main is item:
                issues.append(("nao_credenciado", name, str(line), values["situacao"], "", "",
                               person["guiche"], person["id"]))
        writer.writerow({k: safe(v) for k, v in values.items()})

in_list = {item[2]["id"] for item in items if item[2]}
for person in people:
    if person["id"] in in_list:
        continue
    completed = person["status"] == "completed"
    issues.append(("fora_da_lista", person["name"], "", SITUACAO.get(person["status"], person["status"]),
                   (person.get("completed_at") or "") if completed else "",
                   delivered_by.get(person["id"], "") if completed else "", person["guiche"], person["id"]))

order = ("fora_da_lista", "nao_credenciado", "nao_encontrado")
issues.sort(key=lambda issue: (order.index(issue[0]), normalize(issue[1])))
with open(issues_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f, delimiter=delimiter)
    writer.writerow(["ocorrencia", "nome", "linha_lista", "situacao", "credenciado_em", "credenciado_por",
                     "guiche", "participante_id"])
    writer.writerows([[safe(value) for value in issue] for issue in issues])

# 4) Resumo
count = {kind: sum(issue[0] == kind for issue in issues) for kind in order}
matched = [p for p in people if p["id"] in in_list]
print(f"Lista original: {len(items)} linhas, {len(latest) - len(left)} pessoas; vale a linha da última "
      f"movimentação ({'coluna ' + movement if movement else 'sem coluna de data: a última linha do arquivo'}).")
if left:
    print(f"Inscrições canceladas fora das contas (quem foi credenciado conta, mesmo cancelado): {len(left)}.")
print(f"Pessoas da lista: {len(matched)} encontradas, {sum(p['status'] == 'completed' for p in matched)} "
      f"credenciadas e {count['nao_credenciado']} não credenciadas; {count['nao_encontrado']} não encontradas no "
      f"sistema.")
print(f"Backup (deve bater com o painel): {len(people)} inscritos; "
      f"{sum(p['status'] != 'registered' for p in people)} chegaram; "
      f"{sum(p['status'] == 'completed' for p in people)} credenciados.")
print(f"\nNo credenciamento, mas fora da lista original: {count['fora_da_lista']}")
for issue in issues:
    if issue[0] == "fora_da_lista":
        print(f"  - {issue[1]} ({issue[3]})")
print(f"\nNão credenciados: {count['nao_credenciado']}; não encontrados: {count['nao_encontrado']} "
      f"(nomes no arquivo de pendências).")
print(f"Arquivos: {output_path} e {issues_path}")
