"""Linha de comando: serve, import, user, sync e backup."""
import argparse
import secrets
import threading
from http.server import ThreadingHTTPServer

from . import settings
from .auth import password_hash
from .bootstrap import init_db
from .csv_io import export_csv, import_file
from .db import backup_file, connect
from .event_theme import event_config
from .sheets import sync_sheets_once, sync_worker
from .web.server import App


def main():
    parser = argparse.ArgumentParser(description="Sistema de credenciamento")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    imp = sub.add_parser("import")
    imp.add_argument("file")
    user = sub.add_parser("user")
    user.add_argument("username")
    user.add_argument("role", choices=("volunteer", "attendant", "admin"))
    user.add_argument("--guiche", default="")
    sub.add_parser("sync")
    backup = sub.add_parser("backup")
    backup.add_argument("file")
    args = parser.parse_args()
    init_db()
    if args.command == "import":
        import_file(args.file)
    elif args.command == "user":
        import getpass
        if args.role == "attendant" and not args.guiche:
            parser.error("Atendente precisa de --guiche.")
        password = getpass.getpass("Senha (mínimo 10 caracteres): ")
        if len(password) < 10:
            parser.error("Senha curta demais.")
        salt = secrets.token_hex(16)
        with connect() as db:
            db.execute("INSERT INTO users(username,salt,password_hash,role,guiche) VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,role=excluded.role,guiche=excluded.guiche",
                       (args.username.casefold(), salt, password_hash(password, salt), args.role, args.guiche))
        print("Usuário salvo.")
    elif args.command == "sync":
        print(f"{sync_sheets_once()} eventos sincronizados.")
    elif args.command == "backup":
        backup_file(args.file)
    elif args.command == "serve":
        if len(settings.SECRET) < 32:
            parser.error("Defina CHECKIN_SESSION_SECRET com pelo menos 32 caracteres.")
        try:
            event_config()
        except ValueError as exc:
            parser.error(str(exc))
        export_csv()
        threading.Thread(target=sync_worker, daemon=True).start()
        print(f"http://{args.host}:{args.port}")
        ThreadingHTTPServer((args.host, args.port), App).serve_forever()
