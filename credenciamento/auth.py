"""Senhas, sessões assinadas, token CSRF e limite de tentativas."""
import base64
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque

from . import settings

RATE = defaultdict(deque)
RATE_LOCK = threading.Lock()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def sign(data):
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(settings.SECRET, raw, hashlib.sha256).hexdigest()
    return raw.decode() + "." + sig


def unsign(token):
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(settings.SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return data if data.get("exp", 0) >= time.time() else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def allowed(ip, limit=12, window=60):
    with RATE_LOCK:
        q = RATE[ip]
        t = time.time()
        while q and q[0] < t - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


def csrf_token(username):
    return hmac.new(settings.SECRET, (username + ":csrf").encode(), hashlib.sha256).hexdigest()
