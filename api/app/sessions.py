"""Revogação de sessão do lado do servidor.

O token é um JWT sem estado, então não dá para apagar um cookie que já saiu
daqui. O que existe é o ``token_epoch`` do usuário: todo token carrega o epoch
vigente quando foi emitido e ``current_user`` recusa quem apresentar um epoch
diferente do atual. Incrementar o epoch invalida, na hora, tudo que já foi
emitido para aquela conta.

Este módulo é o único lugar que mexe nesse contador — reset de senha, logout e
qualquer revogação administrativa passam por aqui, para o mecanismo não se
duplicar em três versões ligeiramente diferentes.
"""

from __future__ import annotations

from .models import User


def current_epoch(user: User) -> int:
    return int(getattr(user, "token_epoch", 0) or 0)


def revoke_sessions(user: User) -> int:
    """Derruba todas as sessões do usuário. Devolve o novo epoch.

    O chamador é responsável pelo ``commit``.
    """
    user.token_epoch = current_epoch(user) + 1
    return user.token_epoch
