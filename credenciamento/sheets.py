"""Cópia de segurança no Google Sheets a partir da fila sheet_outbox."""
import json
import time
import urllib.error
import urllib.request

from . import settings
from .common import now
from .db import connect


def sync_sheets_once():
    if not settings.SHEET_URL or not settings.SHEET_SECRET:
        return 0
    with connect() as db:
        rows = db.execute("SELECT event_id,payload FROM sheet_outbox WHERE delivered_at IS NULL ORDER BY rowid LIMIT 40").fetchall()
    if not rows:
        return 0
    payload = {"secret": settings.SHEET_SECRET, "records": [json.loads(r["payload"]) for r in rows]}
    request = urllib.request.Request(settings.SHEET_URL, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise ValueError(result.get("error", "Google Sheets recusou a sincronização"))
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET delivered_at=?,attempts=attempts+1,last_error='' WHERE event_id=?",
                           [(now(), r["event_id"]) for r in rows])
        return len(rows)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        with connect() as db:
            db.executemany("UPDATE sheet_outbox SET attempts=attempts+1,last_error=? WHERE event_id=?",
                           [(str(exc)[:300], r["event_id"]) for r in rows])
        return 0


def sync_worker():
    while True:
        sync_sheets_once()
        time.sleep(15)
