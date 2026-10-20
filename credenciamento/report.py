"""Relatório de credenciados: a lista original de inscrição (com CPF e e-mail) de volta, com a
situação de cada pessoa ao lado.

O banco e o backup só têm as chaves de busca, nunca o CPF ou o e-mail em texto. O relatório
calcula as chaves de cada linha da lista original com a CHECKIN_LOOKUP_SECRET deste servidor e
procura a pessoa por nome e e-mail (como a importação), depois por CPF e, por fim, pelo e-mail
quando só uma pessoa o usa."""
import csv
import io
import json
from pathlib import Path

from .common import normalize
from .csv_io import csv_safe
from .db import connect
from .lookup import cpf_digits, cpf_key, email_key
from .snapshot import FORMAT, lookup_fingerprint

REPORT_COLUMNS = ("credenciado", "credenciado_em", "credenciado_por", "buscado_por", "guiche", "situacao")


def _read_list(path):
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        rows = json.loads(content)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("A lista em JSON deve ser uma lista de registros.")
        fields = list(dict.fromkeys(key for row in rows for key in row))
        return fields, [{key: "" if row.get(key) is None else str(row.get(key)) for key in fields} for row in rows], ","
    for delimiter in (",", ";", "\t"):
        reader = csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter)
        fields = [field.strip() for field in reader.fieldnames or []]
        if "nome" in fields and "email" in fields:
            reader.fieldnames = fields
            return fields, [row for row in reader if any(str(v or "").strip() for v in row.values())], delimiter
    raise ValueError("A lista original precisa das colunas nome e email (separadas por vírgula, ponto e vírgula "
                     "ou tabulação).")


def _load_backup(path):
    try:
        backup = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        backup = None
    if not isinstance(backup, dict) or backup.get("format") != FORMAT:
        raise ValueError("Arquivo não é um backup do credenciamento.")
    if backup.get("lookup") != lookup_fingerprint():
        raise ValueError("Este backup foi feito com outra chave de busca (CHECKIN_LOOKUP_SECRET): "
                         "nenhum CPF ou e-mail da lista seria reconhecido.")
    return backup.get("participants") or [], backup.get("events") or []


def _load_database():
    with connect() as db:
        people = [dict(row) for row in db.execute("SELECT * FROM participants")]
        events = [dict(row) for row in db.execute(
            "SELECT participant_id, action, actor, occurred_at FROM events ORDER BY occurred_at, rowid")]
    return people, events


def credentialed_report(list_path, output_path, backup_path=None):
    """Grava em output_path a lista original com as colunas de REPORT_COLUMNS; usa o backup quando
    informado, senão o banco atual. Devolve (linhas, credenciados, não encontrados)."""
    list_path = Path(list_path)
    fields, rows, delimiter = _read_list(list_path)
    people, events = _load_backup(backup_path) if backup_path else _load_database()
    # Quem entregou o kit: o último "complete" de cada pessoa (um undo_complete seguido de nova
    # retirada gera outro).
    delivered_by = {event["participant_id"]: event["actor"] for event in events if event.get("action") == "complete"}
    by_name_email = {(person["name_key"], person["email_key"]): person for person in people}
    by_cpf = {person["cpf_key"]: person for person in people if person.get("cpf_key")}
    by_email = {}
    for person in people:
        by_email.setdefault(person["email_key"], []).append(person)

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields + [c for c in REPORT_COLUMNS if c not in fields],
                            delimiter=delimiter, extrasaction="ignore")
    writer.writeheader()
    done = missing = 0
    for row in rows:
        email, cpf = str(row.get("email") or "").strip(), cpf_digits(row.get("cpf"))
        mail = email_key(email.casefold()) if email else ""
        same_email = by_email.get(mail, [])
        person = (by_name_email.get((normalize(row.get("nome")), mail))
                  or (by_cpf.get(cpf_key(cpf)) if len(cpf) == 11 else None)
                  or (same_email[0] if len(same_email) == 1 else None))
        values = {key: row.get(key, "") for key in fields}
        if person is None:
            missing += 1
            values.update(credenciado="não encontrado")
        else:
            completed = person["status"] == "completed"
            done += completed
            values.update(credenciado="sim" if completed else "não",
                          credenciado_em=(person.get("completed_at") or "") if completed else "",
                          credenciado_por=delivered_by.get(person["id"], "") if completed else "",
                          buscado_por=person.get("claimed_by") or "", guiche=person["guiche"],
                          situacao=person["status"])
        writer.writerow({key: csv_safe(value) for key, value in values.items()})
    Path(output_path).write_bytes(b"\xef\xbb\xbf" + output.getvalue().encode("utf-8"))
    return len(rows), done, missing
