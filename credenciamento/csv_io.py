"""Importação e exportações em CSV/JSON, incluindo o espelho local participantes.csv."""
import csv
import io
import json
import os
import re
import threading
import uuid
from pathlib import Path

from . import settings
from .common import normalize, now
from .db import connect, get_participant, record_event, update_participant
from .desks import configured_ranges, guiche_for, priority_guiche
from .lookup import cpf_digits, cpf_key, email_key, is_key

CSV_LOCK = threading.Lock()


FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe(value):
    """Keep spreadsheets from running cell content as a formula."""
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES) else value


def export_csv():
    """The local CSV is a restart-rebuilt mirror; SQLite remains the source of truth."""
    with CSV_LOCK, connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
        fields = ["id", "name", "badge_name", "cpf_prefix", "affiliation", "paid", "priority", "guiche", "status",
                  "claimed_by", "prechecked_at", "claimed_at", "ready_at", "completed_at",
                  "updated_at", "revision"]
        temp = settings.CSV.with_suffix(".csv.tmp")
        with temp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: csv_safe(row[key]) for key in fields})
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, settings.CSV)

# A exportação não tem CPF nem e-mail, só as chaves de busca: reimportada, mantém as mesmas pessoas.
PARTICIPANT_COLUMNS = ("id", "nome", "nome_cracha", "afiliacao", "email_hash", "cpf_hash", "cpf_inicio",
                       "pago", "prioridade", "guiche")

PARTICIPANT_EXPORT_COLUMNS = PARTICIPANT_COLUMNS + ("situacao", "responsavel", "pre_checkin_em",
                                                      "busca_iniciada_em", "pronto_em", "retirado_em",
                                                      "atualizado_em", "revisao")

PARTICIPANT_TEMPLATE_COLUMNS = ("nome", "nome_cracha", "afiliacao", "email", "cpf", "pago", "prioridade")

EVENT_LOG_COLUMNS = ("evento_id", "participante_id", "nome", "nome_cracha", "guiche",
                     "acao", "responsavel", "data_hora")


def participants_download():
    """Export participant data, including workflow fields accepted on reimport."""
    with connect() as db:
        rows = db.execute("SELECT * FROM participants ORDER BY name_key").fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        values = {"id": row["id"], "nome": row["name"], "nome_cracha": row["badge_name"],
                  "afiliacao": row["affiliation"], "email_hash": row["email_key"],
                  "cpf_hash": row["cpf_key"], "cpf_inicio": row["cpf_prefix"],
                  "pago": row["paid"], "prioridade": row["priority"], "guiche": row["guiche"],
                  "situacao": row["status"], "responsavel": row["claimed_by"],
                  "pre_checkin_em": row["prechecked_at"], "busca_iniciada_em": row["claimed_at"],
                  "pronto_em": row["ready_at"], "retirado_em": row["completed_at"],
                  "atualizado_em": row["updated_at"], "revisao": row["revision"]}
        writer.writerow({key: csv_safe(value) for key, value in values.items()})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def participants_template_download():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PARTICIPANT_TEMPLATE_COLUMNS)
    writer.writeheader()
    writer.writerow({"nome": "Maria da Silva", "nome_cracha": "Maria", "afiliacao": "Universidade Exemplo",
                     "email": "maria@example.org", "cpf": "12345678901", "pago": "1", "prioridade": "0"})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def event_logs_download():
    with connect() as db:
        rows = db.execute("""
            SELECT e.id AS evento_id, e.participant_id AS participante_id, p.name AS nome,
                   p.badge_name AS nome_cracha, p.guiche,
                   e.action AS acao, e.actor AS responsavel, e.occurred_at AS data_hora
            FROM events e
            LEFT JOIN participants p ON p.id=e.participant_id
            ORDER BY e.occurred_at DESC, e.id DESC
        """).fetchall()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EVENT_LOG_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: csv_safe(row[key] or "") for key in EVENT_LOG_COLUMNS})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def import_file(path):
    path = Path(path)
    result = import_text(path.read_text(encoding="utf-8-sig"), path.suffix.lower())
    print(f"{result[0]} registros lidos; {result[1]} criados ou atualizados.")
    return result


def import_text(content, suffix, actor="system"):
    if suffix == ".json":
        records = json.loads(content.lstrip("\ufeff"))
        if isinstance(records, dict):
            records = records.get("participants", [])
    elif suffix == ".csv":
        content = content.lstrip("\ufeff")
        readers = [csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter)
                   for delimiter in (",", ";", "\t")]
        # O e-mail vem em texto (lista original) ou como chave (arquivo exportado pelo painel).
        reader = next((candidate for candidate in readers
                       if {field.strip() for field in candidate.fieldnames or []} & {"email", "email_hash"}
                       and "nome" in {field.strip() for field in candidate.fieldnames or []}), None)
        if reader is None:
            raise ValueError("Cabeçalho CSV inválido: use nome e email como colunas. "
                             "Separe as colunas por vírgula, ponto e vírgula ou tabulação.")
        fields = [field.strip() for field in reader.fieldnames]
        if len(fields) != len(set(fields)):
            raise ValueError("Cabeçalho CSV contém colunas duplicadas.")
        reader.fieldnames = fields
        records = [item for item in reader if any(str(value or "").strip() for value in item.values())]
    else:
        raise ValueError("Use um arquivo CSV ou JSON.")
    if not isinstance(records, list) or not records:
        raise ValueError("Arquivo não contém participantes. Inclua pelo menos uma linha de dados além do cabeçalho.")
    prepared = []
    seen, seen_ids = set(), set()
    priority_desk = priority_guiche()
    try:
        ranges = configured_ranges()
    except ValueError:
        ranges = []
    for n, item in enumerate(records, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Linha {n}: registro inválido.")
        def value(key):
            raw = item.get(key, "")
            if raw is None:
                return ""
            raw = str(raw).strip()
            return raw[1:] if raw.startswith("'") and raw[1:].lstrip().startswith(FORMULA_PREFIXES) else raw

        pid = value("id")
        if pid:
            try:
                pid = str(uuid.UUID(pid))
            except ValueError as exc:
                raise ValueError(f"Linha {n}: id inválido.") from exc
            if pid in seen_ids:
                raise ValueError(f"Linha {n}: id duplicado.")
            seen_ids.add(pid)
        name, badge_name = value("nome"), value("nome_cracha")
        affiliation, email, cpf = value("afiliacao"), value("email"), value("cpf")
        if not name or not (email or value("email_hash")):
            raise ValueError(f"Linha {n}: nome e email são obrigatórios.")
        if not badge_name:
            name_parts = name.split()
            badge_name = name_parts[0] if len(name_parts) == 1 else f"{name_parts[0]} {name_parts[-1]}"
        if cpf and not re.fullmatch(r"(?:\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})", cpf):
            raise ValueError(f"Linha {n}: cpf deve ter 11 dígitos ou usar o formato xxx.xxx.xxx-xx.")
        if email and "@" not in email:
            raise ValueError(f"Linha {n}: email inválido.")
        # Só as chaves de busca são guardadas; CPF e e-mail em texto param aqui.
        if email:
            mail_key = email_key(email)
        elif is_key(value("email_hash").lower()):
            mail_key = value("email_hash").lower()
        else:
            raise ValueError(f"Linha {n}: email_hash inválido.")
        if cpf:
            id_key, cpf_prefix = cpf_key(cpf), cpf_digits(cpf)[:3]
        elif value("cpf_hash"):
            id_key, cpf_prefix = value("cpf_hash").lower(), value("cpf_inicio")
            if not is_key(id_key) or not re.fullmatch(r"\d{3}", cpf_prefix):
                raise ValueError(f"Linha {n}: cpf_hash ou cpf_inicio inválido.")
        else:
            id_key, cpf_prefix = "", ""
        paid_raw, priority_raw = value("pago"), value("prioridade")
        if paid_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: pago deve ser 0 ou 1.")
        if priority_raw not in ("", "0", "1"):
            raise ValueError(f"Linha {n}: prioridade deve ser 0 ou 1.")
        paid, priority = int(paid_raw or 0), int(priority_raw or 0)
        explicit = value("guiche")
        if priority and priority_desk:
            # Prioridade manda: quem credencia define a prioridade, e ela vale mais que a coluna guiche.
            guiche, manual = priority_desk, 0
        else:
            try:
                automatic = guiche_for(name, ranges)
            except ValueError:
                if not explicit:
                    raise
                automatic = ""
            # An exported file repeats the computed desk; only a different value is a manual choice.
            guiche, manual = explicit or automatic, int(bool(explicit) and explicit != automatic)
        key = (normalize(name), mail_key)
        if len(key[0]) < 3 or not guiche:
            raise ValueError(f"Linha {n}: nome e email devem ser válidos; guiche deve ser informado ou calculável.")
        if key in seen:
            raise ValueError(f"Linha {n}: nome e e-mail duplicados.")
        seen.add(key)
        prepared.append((pid, name, badge_name, key[0], key[1], id_key, cpf_prefix, affiliation, paid, priority,
                         guiche, manual))
    count = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for pid, name, badge_name, name_key, mail_key, id_key, cpf_prefix, affiliation, paid, priority, guiche, manual in prepared:
            old_by_id = get_participant(db, pid) if pid else None
            old_by_key = db.execute("SELECT * FROM participants WHERE name_key=? AND email_key=?",
                                    (name_key, mail_key)).fetchone()
            if old_by_id and old_by_key and old_by_id["id"] != old_by_key["id"]:
                raise ValueError(f"{name}: id e nome/e-mail identificam pessoas diferentes.")
            old = old_by_id or old_by_key
            if old:
                if any(old[k] != v for k, v in (("name", name), ("name_key", name_key),
                                                  ("badge_name", badge_name), ("email_key", mail_key),
                                                  ("cpf_key", id_key), ("cpf_prefix", cpf_prefix),
                                                  ("affiliation", affiliation), ("paid", paid),
                                                  ("priority", priority), ("guiche", guiche))):
                    if old["guiche"] != guiche and old["status"] in ("searching", "ready", "completed"):
                        raise ValueError(f"Não é possível mudar o guichê de {name}: busca ou retirada já iniciada.")
                    update_participant(db, old["id"], "import_update", actor, name=name, name_key=name_key,
                                       badge_name=badge_name, email_key=mail_key, cpf_key=id_key,
                                       cpf_prefix=cpf_prefix,
                                       affiliation=affiliation, paid=paid, priority=priority, guiche=guiche)
                    count += 1
                if old["guiche_manual"] != manual:
                    db.execute("UPDATE participants SET guiche_manual=? WHERE id=?", (manual, old["id"]))
            else:
                pid = pid or str(uuid.uuid4())
                db.execute("INSERT INTO participants(id,name,badge_name,name_key,email_key,cpf_key,cpf_prefix,affiliation,paid,priority,guiche,guiche_manual,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, name, badge_name, name_key, mail_key, id_key, cpf_prefix, affiliation, paid, priority, guiche, manual, now()))
                record_event(db, get_participant(db, pid), "import", actor)
                count += 1
        db.commit()
    export_csv()
    return len(prepared), count
