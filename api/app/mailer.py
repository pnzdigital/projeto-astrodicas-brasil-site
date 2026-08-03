import html
import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _get_config() -> dict[str, Any]:
    """Load config from environment."""
    return {
        "api_key": os.getenv("RESEND_API_KEY", ""),
        "mail_from": os.getenv("MAIL_FROM", "AstroDicas <naoresponda@pnzdigital.com.br>"),
        "portal_url": os.getenv("PORTAL_URL", "https://dash.astrodicas.pnzdigital.com.br/"),
        "timeout": int(os.getenv("RESEND_TIMEOUT_SECONDS", "15")),
    }


def _send_email(
    to: str,
    subject: str,
    html_content: str,
    text_content: str,
) -> dict[str, Any]:
    """Send email via Resend API. Never raises exceptions."""
    config = _get_config()

    if not config["api_key"]:
        logger.warning("RESEND_API_KEY not set")
        return {"sent": False, "error": "RESEND_API_KEY ausente"}

    try:
        payload = {
            "from": config["mail_from"],
            "to": to,
            "subject": subject,
            "html": html_content,
            "text": text_content,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            RESEND_ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
                "User-Agent": "AstroDicas/1.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=config["timeout"]) as response:
            data = json.loads(response.read().decode("utf-8"))
            logger.info(f"Email sent to {to}: {data.get('id', 'unknown')}")
            return {"sent": True, "id": data.get("id")}
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8") if e.fp else str(e)
        logger.warning(f"Resend HTTP error for {to}: {error_msg}")
        return {"sent": False, "error": f"HTTP {e.code}: {error_msg}"}
    except urllib.error.URLError as e:
        logger.warning(f"Resend network error for {to}: {e.reason}")
        return {"sent": False, "error": f"Network error: {e.reason}"}
    except Exception as e:
        logger.warning(f"Unexpected error sending email to {to}: {e}")
        return {"sent": False, "error": str(e)}


def _escape_html(text: str) -> str:
    """Escape HTML entities in user input."""
    return html.escape(text, quote=True)


def _html_template(
    content: str,
    footer_text: str,
) -> str:
    """Base HTML template with AstroDicas branding."""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AstroDicas</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f0f0f0;
            color: #333;
        }}
        .container {{
            max-width: 600px;
            margin: 20px auto;
            background-color: #080719;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            background: linear-gradient(135deg, #080719 0%, #1a1629 100%);
            padding: 40px 20px;
            text-align: center;
            border-bottom: 2px solid #d9ac5c;
        }}
        .header h1 {{
            color: #d9ac5c;
            margin: 0;
            font-size: 28px;
            font-weight: bold;
            font-family: 'Georgia', serif;
        }}
        .content {{
            padding: 40px 30px;
            color: #f4efe4;
            line-height: 1.6;
        }}
        .content h2 {{
            color: #d9ac5c;
            font-family: 'Georgia', serif;
            font-size: 20px;
            margin-top: 0;
            margin-bottom: 20px;
        }}
        .cta-button {{
            display: inline-block;
            background-color: #d9ac5c;
            color: #080719;
            padding: 12px 30px;
            border-radius: 4px;
            text-decoration: none;
            font-weight: bold;
            font-size: 14px;
            margin-top: 20px;
            transition: background-color 0.3s;
        }}
        .cta-button:hover {{
            background-color: #e6c173;
        }}
        .info-box {{
            background-color: #1a1629;
            border-left: 3px solid #d9ac5c;
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }}
        .info-box strong {{
            color: #d9ac5c;
        }}
        .footer {{
            background-color: #0d0410;
            padding: 30px;
            text-align: center;
            border-top: 1px solid #d9ac5c;
            font-size: 12px;
            color: #999;
        }}
        .footer a {{
            color: #d9ac5c;
            text-decoration: none;
        }}
        .divider {{
            height: 1px;
            background-color: #d9ac5c;
            margin: 20px 0;
            opacity: 0.3;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✨ AstroDicas ✨</h1>
        </div>
        <div class="content">
            {content}
        </div>
        <div class="footer">
            <p>{footer_text}</p>
            <p style="margin-top: 10px;">© 2024 AstroDicas. Todos os direitos reservados.</p>
        </div>
    </div>
</body>
</html>
"""


def send_welcome(
    email: str,
    name: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Send welcome email to new user."""
    config = _get_config()
    name_safe = _escape_html(name)
    portal_url = config["portal_url"]

    if locale == "es-AR":
        subject = "¡Bienvenido a AstroDicas!"
        content = f"""<h2>¡Hola, {name_safe}!</h2>
<p>Gracias por unirte a AstroDicas. Estamos muy emocionados de tenerte con nosotros.</p>
<p>Tu cuenta ha sido creada exitosamente. Ahora podés acceder al portal de AstroDicas para explorar lecturas personalizadas basadas en tu carta astral.</p>
<a href="{portal_url}" class="cta-button">Acceder al Portal</a>
<p>Si tenés preguntas, no dudes en contactarnos.</p>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Hola, {name_safe}!

Gracias por unirte a AstroDicas. Estamos muy emocionados de tenerte con nosotros.

Tu cuenta ha sido creada exitosamente. Ahora podés acceder al portal de AstroDicas.

Portal: {portal_url}

¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br
"""
    else:  # pt-BR
        subject = "Bem-vindo(a) ao AstroDicas!"
        content = f"""<h2>Olá, {name_safe}!</h2>
<p>Obrigado por se juntar ao AstroDicas. Estamos muito felizes em tê-lo conosco.</p>
<p>Sua conta foi criada com sucesso. Agora você pode acessar o portal do AstroDicas para explorar leituras personalizadas baseadas no seu mapa astral.</p>
<a href="{portal_url}" class="cta-button">Acessar o Portal</a>
<p>Se tiver dúvidas, não hesite em nos contatar.</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Olá, {name_safe}!

Obrigado por se juntar ao AstroDicas. Estamos muito felizes em tê-lo conosco.

Sua conta foi criada com sucesso. Agora você pode acessar o portal do AstroDicas.

Portal: {portal_url}

Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br
"""

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_purchase_confirmation(
    email: str,
    name: str,
    product_title: str,
    amount_label: str,
    locale: str = "pt-BR",
    temp_password: str | None = None,
) -> dict[str, Any]:
    """Send purchase confirmation email."""
    config = _get_config()
    name_safe = _escape_html(name)
    product_safe = _escape_html(product_title)
    amount_safe = _escape_html(amount_label)
    portal_url = config["portal_url"]

    if locale == "es-AR":
        subject = "¡Tu compra en AstroDicas está confirmada!"
        password_section = ""
        if temp_password:
            password_section = f"""
<div class="info-box">
    <strong>Contraseña temporal:</strong> <code>{_escape_html(temp_password)}</code>
    <p>Por favor, cambia tu contraseña en el portal cuando accedas por primera vez.</p>
</div>
"""
        content = f"""<h2>¡Compra confirmada!</h2>
<p>Hola {name_safe},</p>
<p>Gracias por tu compra en AstroDicas. Hemos recibido tu pago exitosamente.</p>
<div class="info-box">
    <strong>Producto liberado:</strong> {product_safe}<br>
    <strong>Monto:</strong> {amount_safe}
</div>
<p>Tu acceso a este contenido está disponible ahora en tu cuenta. Podés acceder al portal para explorar tu lectura personalizada.</p>
{password_section}
<a href="{portal_url}" class="cta-button">Ir al Portal</a>
<p>¡Que disfrutes tu lectura astrológica!</p>
"""
        footer = "¿Preguntas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = f"""¡Compra confirmada!

Hola {name_safe},

Gracias por tu compra en AstroDicas. Hemos recibido tu pago exitosamente.

Producto liberado: {product_safe}
Monto: {amount_safe}

Tu acceso está disponible ahora en tu cuenta.
"""
        if temp_password:
            text_content += f"\nContraseña temporal: {temp_password}\nPor favor, cambia tu contraseña cuando accedas."
        text_content += f"\n\nPortal: {portal_url}\n\n¿Preguntas? Escribinos a contato@astrodicas.pnzdigital.com.br"
    else:  # pt-BR
        subject = "Sua compra no AstroDicas foi confirmada!"
        password_section = ""
        if temp_password:
            password_section = f"""
<div class="info-box">
    <strong>Senha temporária:</strong> <code>{_escape_html(temp_password)}</code>
    <p>Por favor, altere sua senha no portal ao acessar pela primeira vez.</p>
</div>
"""
        content = f"""<h2>Compra confirmada!</h2>
<p>Olá {name_safe},</p>
<p>Obrigado pela sua compra no AstroDicas. Recebemos seu pagamento com sucesso.</p>
<div class="info-box">
    <strong>Produto liberado:</strong> {product_safe}<br>
    <strong>Valor:</strong> {amount_safe}
</div>
<p>Seu acesso a este conteúdo está disponível agora em sua conta. Você pode acessar o portal para explorar sua leitura personalizada.</p>
{password_section}
<a href="{portal_url}" class="cta-button">Ir para o Portal</a>
<p>Aproveite sua leitura astrológica!</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Compra confirmada!

Olá {name_safe},

Obrigado pela sua compra no AstroDicas. Recebemos seu pagamento com sucesso.

Produto liberado: {product_safe}
Valor: {amount_safe}

Seu acesso está disponível agora em sua conta.
"""
        if temp_password:
            text_content += f"\nSenha temporária: {temp_password}\nPor favor, altere sua senha ao acessar."
        text_content += f"\n\nPortal: {portal_url}\n\nDúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_reading_ready(
    email: str,
    name: str,
    reading_title: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Send notification that reading is ready."""
    config = _get_config()
    name_safe = _escape_html(name)
    reading_safe = _escape_html(reading_title)
    portal_url = config["portal_url"]

    if locale == "es-AR":
        subject = "¡Tu lectura en AstroDicas está lista!"
        content = f"""<h2>¡Tu lectura está lista!</h2>
<p>Hola {name_safe},</p>
<p>Tenemos el placer de informarte que tu lectura astrológica ha sido generada y está lista para ser explorada.</p>
<div class="info-box">
    <strong>Lectura:</strong> {reading_safe}
</div>
<p>Accede a tu portal para ver todos los detalles personalizados de tu lectura astral.</p>
<a href="{portal_url}" class="cta-button">Ver Mi Lectura</a>
<p>¡Esperamos que disfrutes las revelaciones que esta lectura te trae!</p>
"""
        footer = "¿Preguntas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = f"""¡Tu lectura está lista!

Hola {name_safe},

Tu lectura astrológica: {reading_safe} ha sido generada y está lista para explorar.

Portal: {portal_url}

¿Preguntas? Escribinos a contato@astrodicas.pnzdigital.com.br
"""
    else:  # pt-BR
        subject = "Sua leitura no AstroDicas está pronta!"
        content = f"""<h2>Sua leitura está pronta!</h2>
<p>Olá {name_safe},</p>
<p>Temos o prazer de informar que sua leitura astrológica foi gerada e está pronta para ser explorada.</p>
<div class="info-box">
    <strong>Leitura:</strong> {reading_safe}
</div>
<p>Acesse seu portal para ver todos os detalhes personalizados de sua leitura astral.</p>
<a href="{portal_url}" class="cta-button">Ver Minha Leitura</a>
<p>Esperamos que você aproveite as revelações que esta leitura traz!</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Sua leitura está pronta!

Olá {name_safe},

Sua leitura astrológica: {reading_safe} foi gerada e está pronta para explorar.

Portal: {portal_url}

Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br
"""

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)
