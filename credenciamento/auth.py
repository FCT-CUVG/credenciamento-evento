"""Senhas, sessões, token CSRF e limites de tentativas."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from . import settings

RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()
SESSION_SECONDS = 12 * 3600
DUMMY_SALT = "00" * 16


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def check_password(user, password):
    """Confere a senha; para usuário inexistente gasta o mesmo tempo, sem revelar que ele não existe."""
    salt, expected = (user["salt"], user["password_hash"]) if user else (DUMMY_SALT, "")
    return hmac.compare_digest(password_hash(password, salt), expected) and bool(user)


# Sessões ficam no banco (só o hash do token): sair ou trocar a senha encerra de fato.
def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, username):
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    db.execute("INSERT INTO sessions(token_hash,username,expires_at) VALUES (?,?,?)",
               (token_hash(token), username, time.time() + SESSION_SECONDS))
    return token


def session_user(db, token):
    if not token:
        return None
    return db.execute("""SELECT u.username, u.role, u.guiche FROM sessions s JOIN users u ON u.username=s.username
                         WHERE s.token_hash=? AND s.expires_at>=?""", (token_hash(token), time.time())).fetchone()


def end_session(db, token):
    db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))


def end_user_sessions(db, username):
    return db.execute("DELETE FROM sessions WHERE username=?", (username,)).rowcount


def csrf_token(session_token):
    return hmac.new(settings.SECRET, ("csrf:" + token_hash(session_token)).encode(), hashlib.sha256).hexdigest()


# Limites de tentativas em memória, por chave ("search:IP", "login-user:nome"...), com a
# quantidade e a janela de settings.LIMITS.
def _recent(key, window, moment):
    q = RATE[key]
    while q and q[0] < moment - window:
        q.popleft()
    return q


def _prune(moment):
    if len(RATE) > 50_000:
        for key in [k for k, q in RATE.items() if not q or q[-1] < moment - 86_400]:
            del RATE[key]


def over_limit(key, name):
    """A chave já atingiu o limite? Não conta esta tentativa (veja record)."""
    limit, window = settings.rate_limit(name)
    with RATE_LOCK:
        return len(_recent(key, window, time.time())) >= limit


def record(key, name):
    _, window = settings.rate_limit(name)
    with RATE_LOCK:
        moment = time.time()
        _recent(key, window, moment).append(moment)
        _prune(moment)


def allowed(key, name):
    """Confere e conta a tentativa de uma vez."""
    limit, window = settings.rate_limit(name)
    with RATE_LOCK:
        moment = time.time()
        q = _recent(key, window, moment)
        if len(q) >= limit:
            return False
        q.append(moment)
        _prune(moment)
        return True


def recent_count(key, name):
    _, window = settings.rate_limit(name)
    with RATE_LOCK:
        return len(_recent(key, window, time.time()))
