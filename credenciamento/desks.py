"""Guichês: faixas de letras, guichê de prioridade e propagação das mudanças de configuração."""
import json
import os
import re
import threading

from . import settings
from .common import normalize
from .db import connect, update_participant

RANGES_LOCK = threading.Lock()


def read_desk_file():
    """Parsed guichês.json; callers that loop should read it once and pass the result along."""
    try:
        config = json.loads(settings.RANGES.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Configuração de guichês inválida: {settings.RANGES}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("ranges"), list):
        raise ValueError(f"Configuração de guichês inválida: {settings.RANGES}")
    return config


def guiche_for(name, ranges=None):
    initial = normalize(name)[:1].upper()
    ranges = configured_ranges() if ranges is None else ranges
    matches = [str(r["guiche"]).strip() for r in ranges
               if str(r["from"]).upper() <= initial <= str(r["to"]).upper()]
    if len(matches) != 1 or not matches[0]:
        raise ValueError(f"Inicial {initial or '?'} de {name!r} sem faixa única de guichê.")
    return matches[0]


def configured_ranges():
    return read_desk_file()["ranges"]

DEFAULT_PRIORITY_GUICHE = "P"


def priority_guiche():
    """Desk for participants marked as priority; an empty value turns it off."""
    try:
        config = read_desk_file()
    except ValueError:
        return DEFAULT_PRIORITY_GUICHE
    value = str(config.get("priority_guiche", DEFAULT_PRIORITY_GUICHE) or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value) else ""


def validate_priority_guiche(value):
    value = str(value or "").strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", value):
        raise ValueError("Guichê de prioridade: use até 24 letras, números, _ ou -.")
    return value


def auto_guiche(name, priority, priority_desk=None, ranges=None):
    """Desk computed from priority and name initial, without explicit assignment."""
    desk = priority_guiche() if priority_desk is None else priority_desk
    return desk if priority and desk else guiche_for(name, ranges)


def desk_ranges(guiche):
    """Letter ranges served by a desk; empty for desks assigned only explicitly."""
    try:
        ranges = configured_ranges()
    except ValueError:
        return []
    return [{"from": str(r.get("from", "")).strip().upper(), "to": str(r.get("to", "")).strip().upper()}
            for r in ranges if isinstance(r, dict) and str(r.get("guiche", "")).strip() == guiche]


def validate_ranges(ranges):
    if not isinstance(ranges, list) or not ranges or len(ranges) > 26:
        raise ValueError("Informe entre 1 e 26 faixas de letras.")
    normalized = []
    coverage = set()
    for index, item in enumerate(ranges, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Faixa {index}: dados inválidos.")
        start = str(item.get("from", "")).strip().upper()
        end = str(item.get("to", "")).strip().upper()
        desk = str(item.get("guiche", "")).strip()
        if not re.fullmatch(r"[A-Z]", start) or not re.fullmatch(r"[A-Z]", end) or start > end:
            raise ValueError(f"Faixa {index}: informe letras de A a Z em ordem.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", desk):
            raise ValueError(f"Faixa {index}: identificador de guichê inválido.")
        letters = set(chr(code) for code in range(ord(start), ord(end) + 1))
        if coverage & letters:
            raise ValueError(f"Faixa {index}: há letras atribuídas a mais de um guichê.")
        coverage.update(letters)
        normalized.append({"from": start, "to": end, "guiche": desk})
    missing = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") - coverage
    if missing:
        raise ValueError("Todas as letras de A a Z precisam ter um guichê. Faltam: " + ", ".join(sorted(missing)))
    return normalized


def save_ranges(ranges, priority=None, actor="system"):
    """Save desk configuration and move participants accordingly; returns how many changed."""
    normalized = validate_ranges(ranges)
    priority = priority_guiche() if priority is None else validate_priority_guiche(priority)
    old_priority = priority_guiche()
    try:
        old_ranges = validate_ranges(configured_ranges())
    except ValueError:
        old_ranges = []
    with RANGES_LOCK:
        temporary = settings.RANGES.with_suffix(settings.RANGES.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps({"ranges": normalized, "priority_guiche": priority},
                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, settings.RANGES)
        finally:
            temporary.unlink(missing_ok=True)
    changed = apply_desk_config(old_ranges, old_priority, normalized, priority, actor)
    return normalized, priority, changed


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def letter_counts(db, priority_desk=None, pending_only=False):
    """Pessoas por inicial entre as que seguem as faixas: fora do guichê de prioridade e sem
    guichê manual. Com pending_only, conta só quem ainda não retirou o kit."""
    desk = priority_guiche() if priority_desk is None else priority_desk
    counts = dict.fromkeys(LETTERS, 0)
    for row in db.execute("SELECT name, priority, status FROM participants WHERE guiche_manual=0").fetchall():
        if (row["priority"] and desk) or (pending_only and row["status"] == "completed"):
            continue
        initial = normalize(row["name"])[:1].upper()
        if initial in counts:
            counts[initial] += 1
    return counts


def balanced_ranges(counts, names):
    """Divide A–Z em len(names) faixas contíguas com totais de pessoas o mais parecidos possível.

    Primeiro acha o menor total possível para o guichê mais cheio; depois, entre as divisões
    que respeitam esse teto, escolhe a de menor soma dos quadrados (totais mais uniformes) e,
    no empate, a de faixas de letras mais parecidas. Uma letra nunca é dividida entre guichês."""
    size, parts = len(LETTERS), len(names)
    if not 1 <= parts <= size:
        raise ValueError(f"Informe entre 1 e {size} guichês.")
    prefix = [0]
    for letter in LETTERS:
        prefix.append(prefix[-1] + counts.get(letter, 0))
    total = lambda start, end: prefix[end] - prefix[start]
    inf = float("inf")
    # peak[j][i]: menor máximo ao dividir as i primeiras letras em j faixas não vazias.
    peak = [[inf] * (size + 1) for _ in range(parts + 1)]
    peak[0][0] = 0
    for j in range(1, parts + 1):
        for i in range(j, size + 1):
            peak[j][i] = min(max(peak[j - 1][p], total(p, i)) for p in range(j - 1, i))
    ceiling = peak[parts][size]
    best = [[None] * (size + 1) for _ in range(parts + 1)]
    best[0][0] = ((0, 0), None)
    for j in range(1, parts + 1):
        for i in range(j, size + 1):
            options = [((best[j - 1][p][0][0] + total(p, i) ** 2, best[j - 1][p][0][1] + (i - p) ** 2), p)
                       for p in range(j - 1, i) if best[j - 1][p] and total(p, i) <= ceiling]
            best[j][i] = min(options) if options else None
    cuts, end = [], size
    for j in range(parts, 0, -1):
        start = best[j][end][1]
        cuts.append((start, end))
        end = start
    return [{"from": LETTERS[start], "to": LETTERS[end - 1], "guiche": name, "total": total(start, end)}
            for name, (start, end) in zip(names, reversed(cuts))]


def balanced_desk_names(count, ranges, priority_desk):
    """Reaproveita os nomes atuais (na ordem das faixas) e completa com números livres."""
    names = []
    for item in ranges:
        desk = str(item.get("guiche", "")).strip() if isinstance(item, dict) else ""
        if desk and desk != priority_desk and desk not in names:
            names.append(desk)
    number = 1
    while len(names) < count:
        if str(number) not in names and str(number) != priority_desk:
            names.append(str(number))
        number += 1
    return names[:count]


def desk_renames(old_ranges, old_priority, new_ranges, new_priority):
    """Desks whose letter range (or priority role) stayed the same but got a new name."""
    new_names = {r["guiche"] for r in new_ranges} | ({new_priority} if new_priority else set())
    old_by_span = {(r["from"], r["to"]): r["guiche"] for r in old_ranges}
    renames, conflicts = {}, set()
    for r in new_ranges:
        old = old_by_span.get((r["from"], r["to"]))
        if old and old != r["guiche"]:
            if renames.setdefault(old, r["guiche"]) != r["guiche"]:
                conflicts.add(old)
    renames = {old: new for old, new in renames.items() if old not in conflicts and old not in new_names}
    if old_priority and new_priority and old_priority != new_priority and old_priority not in new_names:
        renames[old_priority] = new_priority
    return renames


def reconcile_priority_desk(db, ranges, priority_desk, actor):
    """Há um só guichê de prioridade. Um guichê fora da configuração em que todos têm prioridade
    é o guichê de prioridade com um nome antigo: todos (e os atendentes) passam para o nome atual."""
    if not priority_desk:
        return 0
    configured = {r["guiche"] for r in ranges} | {priority_desk}
    changed = 0
    for (desk,) in db.execute("SELECT DISTINCT guiche FROM participants").fetchall():
        if desk in configured:
            continue
        rows = db.execute("SELECT * FROM participants WHERE guiche=?", (desk,)).fetchall()
        if not all(row["priority"] for row in rows):
            continue
        for row in rows:
            update_participant(db, row["id"], "guiche_rename", actor, guiche=priority_desk, guiche_manual=0)
            changed += 1
        db.execute("UPDATE users SET guiche=? WHERE guiche=?", (priority_desk, desk))
    return changed


def apply_desk_config(old_ranges, old_priority, new_ranges, new_priority, actor):
    """Renamed desks follow everyone (the kit is at the same desk); other changes only move
    automatically assigned participants whose kit search has not started."""
    renames = desk_renames(old_ranges, old_priority, new_ranges, new_priority)
    changed = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for old, new in renames.items():
            for row in db.execute("SELECT id FROM participants WHERE guiche=?", (old,)).fetchall():
                update_participant(db, row["id"], "guiche_rename", actor, guiche=new)
                changed += 1
            db.execute("UPDATE users SET guiche=? WHERE guiche=?", (new, old))
        changed += reconcile_priority_desk(db, new_ranges, new_priority, actor)
        # Prioridade manda mesmo sobre guichê manual; os demais manuais ficam onde estão.
        for row in db.execute("SELECT * FROM participants WHERE (guiche_manual=0 OR priority=1) "
                              "AND status IN ('registered','prechecked')").fetchall():
            if row["guiche_manual"] and not (row["priority"] and new_priority):
                continue
            try:
                guiche = auto_guiche(row["name"], row["priority"], new_priority, new_ranges)
            except ValueError:
                continue
            if guiche != row["guiche"]:
                update_participant(db, row["id"], "guiche_reassign", actor, guiche=guiche)
                changed += 1
        db.commit()
    return changed


def available_guiches(db):
    """List configured desks, including desks assigned explicitly to participants or staff."""
    ranges = configured_ranges()
    desks = {}
    priority = priority_guiche()
    if priority:
        desks[priority] = []
    for item in ranges:
        desk = str(item["guiche"]).strip()
        if desk:
            desks.setdefault(desk, []).append(f'{str(item["from"]).upper()}–{str(item["to"]).upper()}')
    for row in db.execute("SELECT guiche FROM participants UNION SELECT guiche FROM users"):
        desk = row["guiche"].strip()
        if desk:
            desks.setdefault(desk, [])
    def order(entry):
        # Ordem natural (1, 1A, 2, 10…), com o guichê de prioridade por último.
        number = re.match(r"(\d+)(.*)", entry[0])
        natural = (0, int(number.group(1)), number.group(2).casefold()) if number else (1, 0, entry[0].casefold())
        return (entry[0] == priority, natural)
    return [{"id": desk, "ranges": labels, "priority": desk == priority} for desk, labels in sorted(desks.items(), key=order)]


def mark_manual_desks(db):
    """Migração: guichês existentes diferentes da regra automática foram escolhidos na importação."""
    try:
        ranges, priority_desk = configured_ranges(), priority_guiche()
    except ValueError:
        ranges, priority_desk = [], ""
    for row in db.execute("SELECT id,name,priority,guiche FROM participants").fetchall():
        try:
            manual = row["guiche"] != auto_guiche(row["name"], row["priority"], priority_desk, ranges)
        except ValueError:
            manual = True
        if manual:
            db.execute("UPDATE participants SET guiche_manual=1 WHERE id=?", (row["id"],))
