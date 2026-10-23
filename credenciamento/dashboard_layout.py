"""Ordem dos blocos do painel detalhado, definida pela coordenação e igual para todas as contas.

O servidor só guarda a lista de identificadores; quem conhece os blocos é a página. Blocos
desconhecidos são ignorados e blocos novos, ausentes da lista, aparecem na posição padrão."""
import re

from .common import now
from .db import get_setting, set_setting

KEY = "dashboard_layout"
MAX_BLOCKS = 30
BLOCK_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")


def dashboard_layout(db):
    return get_setting(db, KEY, {"order": [], "by": None, "at": None})


def save_dashboard_layout(db, order, actor):
    """Grava a ordem (lista vazia volta à ordem padrão) e devolve o que ficou salvo."""
    if not isinstance(order, list) or len(order) > MAX_BLOCKS:
        raise ValueError("Ordem dos blocos inválida.")
    if not all(isinstance(item, str) and BLOCK_ID.fullmatch(item) for item in order):
        raise ValueError("Ordem dos blocos inválida.")
    if len(set(order)) != len(order):
        raise ValueError("Um bloco aparece mais de uma vez na ordem.")
    layout = {"order": order, "by": actor, "at": now()}
    set_setting(db, KEY, layout)
    return layout
