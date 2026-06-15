#!/usr/bin/env python3
"""
============================================================
Definir senha do administrador LOCAL — Automação de Backups
============================================================
Pede a senha de forma OCULTA (não aparece na tela, no chat nem no
histórico do shell), gera o hash (Werkzeug) e grava em
ADM_LOCAL_SENHA_HASH no arquivo .env. A senha em texto puro nunca é
exibida nem armazenada.

Uso (a partir da raiz do projeto):
    venv/bin/python scripts/definir_senha_admin_local.py

Depois, reinicie o servidor para a nova senha valer.
============================================================
"""

import getpass
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

_RAIZ = Path(__file__).resolve().parent.parent
_ENV = _RAIZ / ".env"
_CHAVE = "ADM_LOCAL_SENHA_HASH"


def main() -> int:
    if not _ENV.exists():
        print(f"[ERRO] Arquivo .env não encontrado em {_ENV}")
        return 1

    senha = getpass.getpass("Nova senha do admin local: ")
    if not senha:
        print("[ERRO] Senha vazia — operação cancelada.")
        return 1
    if len(senha) < 8:
        print("[ERRO] Use ao menos 8 caracteres (recomendado: senha forte).")
        return 1
    if senha != getpass.getpass("Confirme a senha: "):
        print("[ERRO] As senhas não conferem — operação cancelada.")
        return 1

    novo_hash = generate_password_hash(senha)

    # Substitui a linha da chave preservando o restante do .env; acrescenta
    # se a chave ainda não existir.
    linhas = _ENV.read_text(encoding="utf-8").splitlines()
    encontrou = False
    for i, linha in enumerate(linhas):
        if linha.startswith(f"{_CHAVE}="):
            linhas[i] = f"{_CHAVE}={novo_hash}"
            encontrou = True
            break
    if not encontrou:
        linhas.append(f"{_CHAVE}={novo_hash}")

    _ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print(f"[OK] Hash gravado em {_CHAVE} no .env.")
    print("     Reinicie o servidor para a nova senha entrar em vigor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
