"""Linha de comando: serve, import, user, revoke, public-checkin, sync e backup."""
import argparse
import secrets
import signal
import threading
from http.server import ThreadingHTTPServer

from . import settings
from .auth import end_user_sessions, password_hash
from .bootstrap import init_db
from .csv_io import export_csv, import_file
from .db import backup_file, connect
from .event_theme import event_config
from .lookup import lookup_ready
from .participants import public_checkin_state, set_public_checkin
from .sheets import sync_sheets_once, sync_worker
from .web.server import App


def stop_server(*_):
    raise KeyboardInterrupt


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
    user.add_argument("--gerar-senha", action="store_true", help="gera e mostra uma senha forte em vez de pedir uma")
    revoke = sub.add_parser("revoke", help="encerra todas as sessões de um usuário (ex.: celular perdido)")
    revoke.add_argument("username")
    public = sub.add_parser("public-checkin", help="abre ou fecha o pré-check-in público")
    public.add_argument("state", choices=("open", "close", "status"))
    sub.add_parser("sync")
    backup = sub.add_parser("backup")
    backup.add_argument("file")
    args = parser.parse_args()
    try:
        init_db()
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "import":
        try:
            import_file(args.file)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.command == "user":
        import getpass
        if args.role == "attendant" and not args.guiche:
            parser.error("Atendente precisa de --guiche.")
        if args.gerar_senha:
            alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
            password = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
        else:
            password = getpass.getpass("Senha (mínimo 10 caracteres): ")
        if len(password) < 10:
            parser.error("Senha curta demais.")
        salt, username = secrets.token_hex(16), args.username.casefold()
        with connect() as db:
            db.execute("INSERT INTO users(username,salt,password_hash,role,guiche) VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,role=excluded.role,guiche=excluded.guiche",
                       (username, salt, password_hash(password, salt), args.role, args.guiche))
            # Senha nova: quem estava conectado com a anterior precisa entrar de novo.
            ended = end_user_sessions(db, username)
        print("Usuário salvo." + (f" {ended} sessão(ões) anterior(es) encerrada(s)." if ended else ""))
        if args.gerar_senha:
            print(f"Senha de {username}: {password}")
    elif args.command == "revoke":
        with connect() as db:
            print(f"{end_user_sessions(db, args.username.casefold())} sessão(ões) encerrada(s).")
    elif args.command == "public-checkin":
        with connect() as db:
            state = (set_public_checkin(db, args.state == "open", "terminal") if args.state != "status"
                     else public_checkin_state(db))
        print(f"Pré-check-in público {'aberto' if state['open'] else 'fechado'}"
              + (f" (por {state['by']} em {state['at']})." if state.get("by") else "."))
    elif args.command == "sync":
        print(f"{sync_sheets_once()} eventos sincronizados.")
    elif args.command == "backup":
        backup_file(args.file)
    elif args.command == "serve":
        if len(settings.SECRET) < 32:
            parser.error("Defina CHECKIN_SESSION_SECRET com pelo menos 32 caracteres.")
        if not lookup_ready():
            parser.error("Defina CHECKIN_LOOKUP_SECRET com pelo menos 32 caracteres.")
        try:
            settings.trusted_proxy_networks()
        except ValueError:
            parser.error("CHECKIN_TRUSTED_PROXY deve ser 1 ou uma lista de IPs/redes separados por vírgula.")
        try:
            for name in settings.LIMITS:
                settings.rate_limit(name)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            event_config()
        except ValueError as exc:
            parser.error(str(exc))
        export_csv()
        threading.Thread(target=sync_worker, daemon=True).start()
        server = ThreadingHTTPServer((args.host, args.port), App)
        # SIGTERM (systemctl stop, docker stop) encerra como o Ctrl+C, sem esperar o SIGKILL, mesmo
        # quando o processo começou com o Ctrl+C ignorado (iniciado em segundo plano por um script).
        signal.signal(signal.SIGTERM, stop_server)
        print(f"http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Servidor encerrado.")
        finally:
            server.server_close()
