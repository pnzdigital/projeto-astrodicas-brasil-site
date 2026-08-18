"""Ponta a ponta com cliente de verdade, contra a API real do MiniMax.

Como rodar:

    cd api && MINIMAX_API_KEY=... .venv/bin/python ../scripts/e2e_cliente.py

Vale antes de deploy que mexa em geração, entitlement ou entrega. Os testes
unitários mockam o MiniMax de propósito (rápidos, determinísticos); este aqui
existe para o que mock nenhum pega: o modelo respondendo de verdade, o worker
puxando job de verdade, o PDF saindo de verdade.


Percorre o caminho da cliente: conta criada pelo pagamento -> login -> perfil
com dados de nascimento -> geração da leitura -> leitura entregue -> PDF.
E confere as TRAVAS: quem não pagou não abre, trial não abre conteúdo pago.

Roda local com sqlite temporário; a chamada ao MiniMax é a de produção.
"""
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

RAIZ = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="astrodicas-e2e-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TMP / 'e2e.db'}"
os.environ["SITE_SECRET_KEY"] = "e2e-only-secret-that-is-at-least-32-bytes"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SITE_ORIGIN"] = "http://testserver"
os.environ["GEOCODING_ENABLED"] = "0"
os.environ["ENV"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ.setdefault("MINIMAX_API_KEY", "")

from fastapi.testclient import TestClient  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Entitlement, Profile, Subscription, User  # noqa: E402
from app.security import hash_password  # noqa: E402
import threading  # noqa: E402
import app.worker as worker  # noqa: E402


def sobe_worker() -> threading.Thread:
    """Worker de verdade, em thread — mesmo laço que roda em produção
    (claim do job, heartbeat, geração, retry). Sem ele o /generate devolve 202
    e o job fica na fila para sempre, que é justamente o desenho do produto."""
    t = threading.Thread(target=worker.worker_loop, daemon=True)
    t.start()
    return t

NAO_LATINO = re.compile(r"[Ѐ-ӿ一-鿿؀-ۿͰ-Ͽ֐-׿฀-๿ऀ-ॿ]")
META = ("não foi possível calcular", "no fue posible calcular", "como assistente", "não posso")

falhas: list[str] = []
notas: list[str] = []


def checa(condicao: bool, descricao: str, detalhe: str = "") -> None:
    marca = "PASS" if condicao else "FALHA"
    linha = f"[{marca}] {descricao}" + (f" — {detalhe}" if detalhe else "")
    print(linha, flush=True)
    if not condicao:
        falhas.append(descricao)


def cria_cliente(email: str, senha: str, produtos: list[str], *, source="site", locale="pt-BR", trial=False) -> str:
    db = SessionLocal()
    try:
        user = User(id=str(uuid4()), email=email, password_hash=hash_password(senha), name="Luciana Teste", locale=locale)
        db.add(user)
        db.flush()
        db.add(Profile(
            user_id=user.id, birth_date=date(1990, 3, 15), birth_time=None,
            birth_city="São Paulo", birth_country="BR", birth_timezone="America/Sao_Paulo",
        ))
        for pid in produtos:
            db.add(Entitlement(
                id=str(uuid4()), user_id=user.id, product_id=pid, status="available",
                source="trial" if trial else source,
                expires_at=datetime.now(timezone.utc) + timedelta(days=3) if trial else None,
            ))
        if trial:
            db.add(Subscription(
                id=str(uuid4()), user_id=user.id, product_id="site:diario_astral", status="trialing",
                provider="local-e2e", external_id=f"e2e-{uuid4()}", locale=locale,
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=3),
                current_period_end=datetime.now(timezone.utc) + timedelta(days=3),
            ))
        db.commit()
        return user.id
    finally:
        db.close()


def login(client: TestClient, email: str, senha: str) -> bool:
    r = client.post("/api/auth/login", json={"email": email, "password": senha})
    return r.status_code == 200


def gera_e_espera(client: TestClient, content_id: str, minutos=6):
    r = client.post(f"/api/me/readings/{content_id}/generate")
    if r.status_code not in (200, 202):
        return r.status_code, None
    limite = time.time() + minutos * 60
    while time.time() < limite:
        listagem = client.get("/api/me/readings").json()
        itens = listagem.get("readings") if isinstance(listagem, dict) else listagem
        for item in itens or []:
            if item.get("content_id") == content_id and item.get("status") in ("ready", "fallback", "failed"):
                return r.status_code, item
        time.sleep(5)
    return r.status_code, None


def main() -> int:
    if not os.environ.get("MINIMAX_API_KEY"):
        print("MINIMAX_API_KEY ausente — abortando (o teste só vale contra a API real)")
        return 2

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sobe_worker()
    print("worker de verdade rodando em thread\n")

    print("\n=== 1. CLIENTE QUE PAGOU O MAPA ASTRAL ===")
    cria_cliente("paga.mapa@example.com", "senha-forte-123", ["site:mapa_astral"])
    with TestClient(app) as c:
        checa(login(c, "paga.mapa@example.com", "senha-forte-123"), "login com a senha certa")
        checa(not login(c, "paga.mapa@example.com", "senha-errada"), "senha errada é recusada")
        login(c, "paga.mapa@example.com", "senha-forte-123")

        acesso = c.get("/api/me/access")
        checa(acesso.status_code == 200, "portal responde o que a cliente tem acesso")
        liberados = {e["product_id"] for e in (acesso.json().get("entitlements") or []) if e.get("active")}
        checa("site:mapa_astral" in liberados, "Mapa Astral liberado para quem pagou", str(liberados))

        codigo, leitura = gera_e_espera(c, "site:content:mapa_astral_completo")
        checa(codigo in (200, 202), "geração aceita", f"HTTP {codigo}")
        checa(leitura is not None, "leitura ficou pronta dentro do tempo")
        if leitura:
            checa(leitura["status"] == "ready", "status final é 'ready'", leitura["status"])
            corpo = leitura.get("body_html") or ""
            if not corpo:
                detalhe = c.get("/api/me/readings/site:content:mapa_astral_completo")
                if detalhe.status_code == 200:
                    corpo = detalhe.json().get("body_html") or ""
            texto = re.sub(r"<[^>]+>", " ", corpo)
            checa(len(texto) > 8000, "texto entregue tem tamanho de produto pago", f"{len(texto)} chars")
            checa(not NAO_LATINO.search(texto), "nenhum caractere fora do alfabeto latino",
                  (NAO_LATINO.search(texto).group() if NAO_LATINO.search(texto) else ""))
            baixo = texto.lower()
            checa(not any(m in baixo for m in META), "nenhum meta-texto de recusa/limitação")
            checa("luciana" in baixo, "usa o nome da cliente")
            checa(baixo.count("casa") >= 5, "cita casas do mapa calculado", f"{baixo.count('casa')} menções")
            checa(len(re.findall(r"<h2|<h3", corpo)) >= 14, "as 15 seções estão no documento",
                  f"{len(re.findall(r'<h2|<h3', corpo))} títulos")
            notas.append(f"amostra: {texto.strip()[:180]}...")

        pdf = c.get("/api/me/readings/site:content:mapa_astral_completo/pdf")
        checa(pdf.status_code == 200, "PDF baixa", f"HTTP {pdf.status_code}")
        checa(pdf.content[:4] == b"%PDF", "arquivo é PDF de verdade", str(pdf.content[:8]))
        checa(len(pdf.content) > 20000, "PDF tem conteúdo", f"{len(pdf.content)} bytes")

        bloqueado = c.post("/api/me/readings/site:content:guia_do_mes/generate")
        checa(bloqueado.status_code in (402, 403), "quem comprou só o Mapa NÃO abre o Guia do Mês",
              f"HTTP {bloqueado.status_code}")

    print("\n=== 2. CLIENTE EM TRIAL (3 dias, sem cartão) ===")
    cria_cliente("trial@example.com", "senha-forte-123", ["site:diario_astral"], trial=True)
    with TestClient(app) as c:
        checa(login(c, "trial@example.com", "senha-forte-123"), "login do trial")
        codigo, leitura = gera_e_espera(c, "site:content:horoscopo_diario", minutos=4)
        checa(codigo in (200, 202), "trial gera o horóscopo diário", f"HTTP {codigo}")
        checa(leitura is not None and leitura["status"] == "ready", "horóscopo do trial fica pronto",
              (leitura or {}).get("status", "não ficou"))
        if leitura:
            corpo = leitura.get("body_html") or ""
            if not corpo:
                d = c.get("/api/me/readings/site:content:horoscopo_diario")
                corpo = d.json().get("body_html", "") if d.status_code == 200 else ""
            texto = re.sub(r"<[^>]+>", " ", corpo)
            checa(not NAO_LATINO.search(texto), "horóscopo sem alfabeto estranho")
            checa(len(texto) > 1200, "horóscopo tem corpo", f"{len(texto)} chars")

        for pago in ("site:content:guia_do_mes", "site:content:previsao_semanal", "site:content:mapa_astral_completo"):
            r = c.post(f"/api/me/readings/{pago}/generate")
            checa(r.status_code in (402, 403), f"trial NÃO abre {pago.split(':')[-1]}", f"HTTP {r.status_code}")

    print("\n=== 3. QUEM NÃO PAGOU NADA ===")
    cria_cliente("sem.acesso@example.com", "senha-forte-123", [])
    with TestClient(app) as c:
        login(c, "sem.acesso@example.com", "senha-forte-123")
        for pago in ("site:content:mapa_astral_completo", "site:content:horoscopo_diario"):
            r = c.post(f"/api/me/readings/{pago}/generate")
            checa(r.status_code in (402, 403), f"sem acesso NÃO abre {pago.split(':')[-1]}", f"HTTP {r.status_code}")

    with TestClient(app) as c:
        r = c.get("/api/me/readings")
        checa(r.status_code == 401, "sem login não lê nada", f"HTTP {r.status_code}")

    print("\n" + "=" * 60)
    for n in notas:
        print(n)
    if falhas:
        print(f"\n{len(falhas)} FALHA(S): " + "; ".join(falhas))
        return 1
    print("\nTUDO PASSOU")
    return 0


if __name__ == "__main__":
    sys.exit(main())
