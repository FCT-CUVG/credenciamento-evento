"""Tabela de rotas da API: cada rota declara o método, o caminho e quem pode usá-la.

O servidor faz as checagens em um lugar só (ver App.dispatch):
- sem papéis: rota pública;
- GET com papéis: sem login ou sem o papel exigido -> 401;
- POST com papéis: sem login da equipe -> 401, CSRF inválido -> 403,
  papel insuficiente -> 403 com a mensagem `forbidden` da rota.
"""
from dataclasses import dataclass
from typing import Callable

STAFF = ("volunteer", "attendant", "admin")
ADMIN = ("admin",)


@dataclass(frozen=True)
class Route:
    handler: Callable
    roles: tuple = ()
    forbidden: str = "Você não tem permissão para esta ação."
    max_body: int = 16384
    prefix: bool = False


ROUTES = {}


def route(method, path, roles=(), forbidden=None, max_body=16384, prefix=False):
    """Registra o handler(req, user, data) para o método e caminho (ou prefixo de caminho)."""
    def register(handler):
        options = {"forbidden": forbidden} if forbidden else {}
        ROUTES[(method, path)] = Route(handler, roles, max_body=max_body, prefix=prefix, **options)
        return handler
    return register


def find(method, path):
    exact = ROUTES.get((method, path))
    if exact:
        return exact
    return next((r for (m, p), r in ROUTES.items() if r.prefix and m == method and path.startswith(p)), None)
