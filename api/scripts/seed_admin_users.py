"""Cria ou atualiza contas do painel admin.

Senha nunca vem de argumento nem fica no arquivo: ela é lida de variável de
ambiente (ou digitada na hora), porque argumento de linha de comando aparece no
histórico do shell e em `ps` para qualquer processo da máquina.

Uso:

    ADMIN_SEED_PASSWORD='...' python scripts/seed_admin_users.py noelia --market all
    ADMIN_SEED_PASSWORD='...' python scripts/seed_admin_users.py luciola --market AR

Idempotente: rodar de novo com o mesmo usuário não duplica conta — atualiza a
senha e o escopo. Sem ADMIN_SEED_PASSWORD, pede a senha pelo terminal.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import AdminUser
from app.security import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria ou atualiza conta do painel admin.")
    parser.add_argument("username", help="nome de usuário (será normalizado para minúsculas)")
    parser.add_argument(
        "--market",
        default="all",
        choices=["all", "BR", "AR"],
        help="escopo da conta: all vê os dois mercados e pode filtrar; BR/AR ficam presos ao seu",
    )
    parser.add_argument("--deactivate", action="store_true", help="desativa a conta em vez de criar/atualizar")
    args = parser.parse_args()

    username = args.username.strip().lower()
    if not username:
        print("usuário vazio", file=sys.stderr)
        return 2

    market = None if args.market == "all" else args.market

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        account = db.scalar(select(AdminUser).where(AdminUser.username == username))

        if args.deactivate:
            if not account:
                print(f"conta '{username}' não existe")
                return 1
            account.active = False
            db.commit()
            print(f"conta '{username}' desativada")
            return 0

        password = os.getenv("ADMIN_SEED_PASSWORD", "")
        if not password:
            password = getpass.getpass(f"senha para '{username}': ")
        if len(password) < 12:
            # O painel expõe dado de cliente e receita. Senha curta aqui é o
            # elo fraco de tudo que veio antes.
            print("senha precisa ter ao menos 12 caracteres", file=sys.stderr)
            return 2

        if account:
            account.password_hash = hash_password(password)
            account.market = market
            account.active = True
            acao = "atualizada"
        else:
            db.add(AdminUser(username=username, password_hash=hash_password(password), market=market))
            acao = "criada"
        db.commit()

    print(f"conta '{username}' {acao} — escopo: {args.market}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
