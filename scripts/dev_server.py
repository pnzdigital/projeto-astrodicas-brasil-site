"""Servidor local de desenvolvimento do canal site.

Reproduz o roteamento do nginx de produção (`nginx.runtime.conf`) num único
processo, para conferir o site inteiro sem Docker:

    .venv/bin/python scripts/dev_server.py            # host público (vitrine + vendas)
    ROLE=dash .venv/bin/python scripts/dev_server.py  # host da área logada + /admin

A API roda embutida via uvicorn; `/api/*` é servido pelo mesmo processo.
"""

from __future__ import annotations

import os
import sys

from pathlib import Path
from wsgiref.simple_server import make_server  # noqa: F401  (documenta a intenção)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'dev.db'}")
os.environ.setdefault("SITE_SECRET_KEY", "dev-only-secret")
os.environ.setdefault("COOKIE_SECURE", "0")
os.environ.setdefault("SITE_ORIGIN", "http://localhost:8080")
os.environ.setdefault("SITE_PUBLIC_URL", "http://localhost:8080")
os.environ.setdefault("PORTAL_URL", "http://localhost:8080/index.html")

import uvicorn  # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402

PORTAL = ROOT / "portal-demo"
LANDING = ROOT / "lp-plano-lua"
IS_DASH = os.getenv("ROLE", "public") == "dash"


def page(path: Path):
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/")
@app.get("/es")
@app.get("/es/")
def root():
    return page(PORTAL / ("index.html" if IS_DASH else "storefront.html"))


if not IS_DASH:
    for route in ("/oferta-lua-1", "/es/oferta-lua-1"):
        app.get(route)(lambda: page(LANDING / "v1" / "index.html"))
    for route in ("/oferta-lua-2", "/es/oferta-lua-2"):
        app.get(route)(lambda: page(LANDING / "v2" / "index.html"))
for route in ("/checkout", "/es/checkout"):
    app.get(route)(lambda: page(PORTAL / "checkout.html"))
for route in ("/obrigado", "/es/obrigado"):
    app.get(route)(lambda: page(PORTAL / "obrigado.html"))
for route in ("/termos", "/es/termos"):
    app.get(route)(lambda: page(PORTAL / "termos.html"))
for route in ("/privacidade", "/es/privacidade"):
    app.get(route)(lambda: page(PORTAL / "privacidade.html"))
for route in ("/suporte", "/es/suporte"):
    app.get(route)(lambda: page(PORTAL / "suporte.html"))

if IS_DASH:
    app.get("/admin")(lambda: page(PORTAL / "admin.html"))


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return (PORTAL / ("robots-dash.txt" if IS_DASH else "robots.txt")).read_text()


from fastapi import HTTPException  # noqa: E402


@app.get("/sitemap.xml")
def sitemap():
    if IS_DASH:
        raise HTTPException(status_code=404)
    return FileResponse(PORTAL / "sitemap.xml", media_type="application/xml")


@app.exception_handler(404)
async def not_found(request, exc):
    return FileResponse(PORTAL / "404.html", status_code=404)


app.mount("/lp-plano-lua", StaticFiles(directory=LANDING), name="landing")
app.mount("/", StaticFiles(directory=PORTAL), name="portal")


if __name__ == "__main__":
    (ROOT / "data").mkdir(exist_ok=True)
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
