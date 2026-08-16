"""fulfill_order precisa enfileirar a geração do conteúdo do mapa comprado.

Antes desta mudança, fulfill_order só criava o Entitlement — o cliente nunca
recebia a leitura, a não ser que clicasse no card (e se aquele clique
falhasse, o card ficava "gerando leitura…" para sempre). Ver
main.enqueue_map_generation e main.products_content_ids.
"""

from datetime import date

from sqlalchemy import select

from app import checkout, main
from app.db import SessionLocal
from app.models import GenerationJob, Order, Profile, User
from conftest import create_user


_counter = [0]


def _order_for(user: User, product_id: str) -> Order:
    _counter[0] += 1
    return Order(
        provider="mercadopago",
        external_id=f"test-ext-{_counter[0]}",
        product_id=product_id,
        amount_minor=1000,
        currency="BRL",
        customer_email=user.email,
        locale="pt-BR",
        status="paid",
    )


def test_fulfill_order_enfileira_geracao_do_mapa_quando_perfil_completo(db_session, skip_auto_run):
    user = create_user("mapa-completo@cliente.com", "senha-segura-123", name="Cliente Mapa")
    db_session.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
    db_session.commit()

    order = _order_for(user, "site:mapa_astral")
    db_session.add(order)
    db_session.commit()

    checkout.fulfill_order(db_session, order)

    jobs = db_session.scalars(select(GenerationJob).where(GenerationJob.user_id == user.id)).all()
    assert len(jobs) == 1
    assert jobs[0].content_id == "site:content:mapa_astral_completo"
    assert jobs[0].status == "queued"


def test_fulfill_order_nao_enfileira_sem_perfil_completo(db_session, skip_auto_run):
    """Compra sem data/local de nascimento ainda preenchidos: adia, não quebra."""
    user = create_user("sem-perfil@cliente.com", "senha-segura-123", name="Cliente Sem Perfil")

    order = _order_for(user, "site:mapa_astral")
    db_session.add(order)
    db_session.commit()

    checkout.fulfill_order(db_session, order)

    jobs = db_session.scalars(select(GenerationJob).where(GenerationJob.user_id == user.id)).all()
    assert jobs == []


def test_fulfill_order_combo_enfileira_todos_os_mapas_do_bundle(db_session, skip_auto_run):
    """Combo entrega mapa_astral + mapa_amor_sinastria: os dois conteúdos entram na fila."""
    user = create_user("combo-mapas@cliente.com", "senha-segura-123", name="Cliente Combo")
    db_session.add(Profile(user_id=user.id, birth_date=date(1985, 1, 1), birth_city="Curitiba", birth_country="BR"))
    db_session.commit()

    order = _order_for(user, "site:combo_mapa_astral_amor")
    db_session.add(order)
    db_session.commit()

    checkout.fulfill_order(db_session, order)

    jobs = db_session.scalars(select(GenerationJob).where(GenerationJob.user_id == user.id)).all()
    content_ids = {job.content_id for job in jobs}
    assert content_ids == {"site:content:mapa_astral_completo", "site:content:mapa_do_amor_sinastria"}


def test_fulfill_order_nao_duplica_job_ja_enfileirado(db_session, skip_auto_run):
    """Segunda chamada de fulfill_order (retry de webhook) não duplica o GenerationJob."""
    user = create_user("retry-webhook@cliente.com", "senha-segura-123", name="Cliente Retry")
    db_session.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
    db_session.commit()

    order = _order_for(user, "site:mapa_astral")
    db_session.add(order)
    db_session.commit()

    checkout.fulfill_order(db_session, order)
    checkout.fulfill_order(db_session, order)

    jobs = db_session.scalars(select(GenerationJob).where(GenerationJob.user_id == user.id)).all()
    assert len(jobs) == 1


def test_products_content_ids_nao_inclui_conteudo_periodico():
    """Conteúdo de cadência própria não nasce da compra — nasce do run_daily_pregen.

    Vale para os três: horóscopo diário, Guia do Mês e Previsão Semanal. A
    Previsão Semanal é o caso caro: gerada aqui, sairia sem `target_iso_week`
    no snapshot e seria descartada como desatualizada na primeira visita ao
    portal — uma chamada paga ao modelo por compra, jogada fora.
    """
    assert main.products_content_ids("site:diario_astral") == ()
    # O que a compra de fato entrega continua vindo.
    assert main.products_content_ids("site:mapa_astral") == ("site:content:mapa_astral_completo",)


def test_content_product_continua_igual_apos_a_extracao():
    """content_product() precisa continuar respondendo igual — só a fonte do dict mudou de lugar."""
    assert main.content_product("site:content:mapa_astral_completo") == "site:mapa_astral"
    assert main.content_product("site:content:horoscopo_diario") == "site:diario_astral"
    assert main.content_product("site:content:inexistente") is None
