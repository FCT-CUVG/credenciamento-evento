#!/usr/bin/env python3
"""Ponto de entrada do sistema de credenciamento (sem dependências, com SQLite).

Uso: python3 app.py serve | import ARQUIVO | user NOME PAPEL | sync | backup ARQUIVO
O código fica no pacote credenciamento/."""
from credenciamento.cli import main

if __name__ == "__main__":
    main()
