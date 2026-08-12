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
        logger.error("RESEND_API_KEY ausente — e-mail para %s não foi enviado", to)
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


def send_password_reset(
    email: str,
    reset_token: str,
    expires_at: "datetime",
    locale: str = "pt-BR",
    portal_url: str | None = None,
) -> dict[str, Any]:
    """Envia o link de redefinição de senha. Nunca levanta exceção."""
    config = _get_config()
    portal = (portal_url or config["portal_url"]).rstrip("/")
    # O token é lido por reset.html (PARAMS.get('reset')), não pela raiz do portal:
    # apontar para `{portal}?reset=` entregava o link na index, que ignora o
    # parâmetro e mostra o login — o cliente nunca chegava a trocar a senha.
    link = f"{portal}/reset?reset={reset_token}"
    expires_label = expires_at.strftime("%d/%m/%Y %H:%M UTC")

    if locale == "es-AR":
        subject = "Restablecé tu contraseña de AstroDicas"
        content = f"""<h2>Solicitud de restablecimiento de contraseña.</h2>
<p>Recibimos un pedido para restablecer la contraseña de tu cuenta de AstroDicas.</p>
<p>Si fuiste vos, hacé clic abajo para definir una nueva contraseña. El enlace vence el {expires_label}.</p>
<a href="{link}" class="cta-button">Restablecer contraseña</a>
<p>Si no solicitaste este cambio, podés ignorar este e-mail: tu contraseña sigue siendo la misma.</p>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Solicitud de restablecimiento de contraseña.\n\n"
            f"Si fuiste vos, ingresá al siguiente enlace para definir una nueva contraseña. "
            f"Vence el {expires_label}.\n\n{link}\n\n"
            f"Si no solicitaste este cambio, ignoralo: tu contraseña sigue igual.\n"
        )
    else:  # pt-BR
        subject = "Redefina sua senha do AstroDicas"
        content = f"""<h2>Pedido de redefinição de senha.</h2>
<p>Recebemos um pedido para redefinir a senha da sua conta no AstroDicas.</p>
<p>Se foi você, clique abaixo para definir uma nova senha. O link expira em {expires_label}.</p>
<a href="{link}" class="cta-button">Redefinir senha</a>
<p>Se você não fez essa solicitação, ignore este e-mail: sua senha continua a mesma.</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Pedido de redefinição de senha.\n\n"
            f"Se foi você, acesse o link abaixo para definir uma nova senha. "
            f"Expira em {expires_label}.\n\n{link}\n\n"
            f"Se você não fez essa solicitação, ignore este e-mail: sua senha continua a mesma.\n"
        )

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_existing_account_notice(
    email: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Avisa o dono real de um e-mail que alguém tentou cadastrar outra conta nele.

    Disparado quando o signup recebe um e-mail já cadastrado. Mantemos a
    resposta da API neutra (sem revelar nada ao atacante); este e-mail é a
    única superfície de desambiguação pro usuário legítimo.
    """
    config = _get_config()
    portal_url = config["portal_url"]
    if locale == "es-AR":
        subject = "Alguien intentó registrar tu e-mail en AstroDicas"
        content = f"""<h2>Tu e-mail ya está con nosotros.</h2>
<p>Alguien intentó crear una cuenta nueva en AstroDicas usando tu dirección de e-mail. No se realizó ningún cambio.</p>
<p>Si fuiste vos, simplemente iniciá sesión en el portal para retomar tus lecturas.</p>
<a href="{portal_url}" class="cta-button">Ir al portal</a>
<p>Si no reconocés este intento, te recomendamos cambiar tu contraseña.</p>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Tu e-mail ya está con nosotros.

Alguien intentó crear una cuenta nueva en AstroDicas usando tu dirección de e-mail. No se realizó ningún cambio.

Si fuiste vos, iniciá sesión: {portal_url}

Si no reconocés este intento, te recomendamos cambiar tu contraseña.
"""
    else:  # pt-BR
        subject = "Alguém tentou cadastrar seu e-mail no AstroDicas"
        content = f"""<h2>Seu e-mail já está com a gente.</h2>
<p>Alguém tentou criar uma conta nova no AstroDicas usando seu endereço de e-mail. Nenhuma alteração foi feita.</p>
<p>Se foi você, basta entrar no portal para retomar suas leituras.</p>
<a href="{portal_url}" class="cta-button">Acessar o portal</a>
<p>Se você não reconhece esta tentativa, recomendamos trocar sua senha.</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = f"""Seu e-mail já está com a gente.

Alguém tentou criar uma conta nova no AstroDicas usando seu endereço de e-mail. Nenhuma alteração foi feita.

Se foi você, entre no portal: {portal_url}

Se você não reconhece esta tentativa, recomendamos trocar sua senha.
"""
    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


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


def send_trial_started(
    email: str,
    name: str,
    trial_ends_at: "datetime",
    locale: str = "pt-BR",
    temp_password: str | None = None,
    portal_url: str | None = None,
) -> dict[str, Any]:
    """Boas-vindas ao trial do Diário Astral. Sem cartão, só e-mail."""
    config = _get_config()
    name_safe = _escape_html(name)
    portal = (portal_url or config["portal_url"]).rstrip("/")
    ends_label = trial_ends_at.strftime("%d/%m/%Y")

    if locale == "es-AR":
        subject = "Tu acceso al Diario Astral ya está activo"
        password_section = ""
        if temp_password:
            password_section = f"""
<div class="info-box">
    <strong>Contraseña temporal:</strong> <code>{_escape_html(temp_password)}</code>
    <p>Cambiala en el portal cuando entres por primera vez.</p>
</div>
"""
        content = f"""<h2>¡{name_safe}, tu cielo ya está calculado!</h2>
<p>Tu acceso al <strong>Diario Astral</strong> está activo por 3 días gratis — sin tarjeta, sin cobro.</p>
<div class="info-box">
    <strong>Durante los 3 días:</strong> horóscopo personalizado todos los días.<br>
    <strong>Acceso válido hasta:</strong> {ends_label}<br>
    <strong>Si decidís continuar:</strong> el Mapa Astral Completo en PDF + Guía del Mes + previsión semanal se agregan cuando confirmás la suscripción.
</div>
{password_section}
<a href="{portal}" class="cta-button">Ir al portal →</a>
<p>Si no querés continuar, no hace falta hacer nada — el acceso se cierra solo al vencerse. Sin cobros.</p>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"¡{name_safe}, tu cielo ya está calculado!\n\n"
            f"Tu acceso al Diario Astral está activo por 3 días gratis — sin tarjeta.\n\n"
            f"Durante los 3 días: horóscopo personalizado todos los días.\n"
            f"Válido hasta: {ends_label}\n\n"
        )
        if temp_password:
            text_content += f"Contraseña temporal: {temp_password}\n\n"
        text_content += f"Portal: {portal}\n\nDudas: contato@astrodicas.pnzdigital.com.br"
    else:
        subject = "Seu acesso ao Diário Astral está ativo"
        password_section = ""
        if temp_password:
            password_section = f"""
<div class="info-box">
    <strong>Senha temporária:</strong> <code>{_escape_html(temp_password)}</code>
    <p>Altere no portal ao acessar pela primeira vez.</p>
</div>
"""
        content = f"""<h2>{name_safe}, seu céu já está calculado!</h2>
<p>Seu acesso ao <strong>Diário Astral</strong> está ativo por 3 dias grátis — sem cartão, sem cobrança.</p>
<div class="info-box">
    <strong>Durante os 3 dias:</strong> horóscopo personalizado todos os dias.<br>
    <strong>Acesso válido até:</strong> {ends_label}<br>
    <strong>Se quiser continuar:</strong> o Mapa Astral Completo em PDF + Guia do Mês + previsão da semana entram quando você confirmar a assinatura.
</div>
{password_section}
<a href="{portal}" class="cta-button">Ir para o portal →</a>
<p>Se não quiser continuar, não precisa fazer nada — o acesso fecha sozinho ao vencer. Sem cobranças.</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"{name_safe}, seu céu já está calculado!\n\n"
            f"Seu acesso ao Diário Astral está ativo por 3 dias grátis — sem cartão.\n\n"
            f"Durante os 3 dias: horóscopo personalizado todos os dias.\n"
            f"Válido até: {ends_label}\n\n"
        )
        if temp_password:
            text_content += f"Senha temporária: {temp_password}\n\n"
        text_content += f"Portal: {portal}\n\nDúvidas: contato@astrodicas.pnzdigital.com.br"

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_trial_ending_email(
    email: str,
    name: str,
    trial_ends_at: "datetime",
    subscribe_url: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Avisa que o trial acaba amanhã e convida a assinar o Diário Astral."""
    name_safe = _escape_html(name)
    ends_label = trial_ends_at.strftime("%d/%m/%Y")
    url_safe = _escape_html(subscribe_url)

    if locale == "es-AR":
        subject = "Tu acceso gratis termina mañana — seguí con el Diario Astral"
        content = f"""<h2>Hola, {name_safe}. Tu trial termina mañana.</h2>
<p>Tu acceso gratis al Diario Astral vence el <strong>{ends_label}</strong>.</p>
<p>Si querés seguir recibiendo tu horóscopo diario y desbloquear el resto:</p>
<div class="info-box">
    <strong>Con tus 30 días se agregan:</strong><br>
    ✦ Mapa Astral Completo en PDF (una vez, tuyo para siempre)<br>
    ✦ Guía del Mes<br>
    ✦ Previsión personalizada de la semana<br>
    <br>
    Es una compra única de 30 días: no queda tarjeta guardada ni se renueva solo.
</div>
<a href="{url_safe}" class="cta-button">Activar el Diario Astral →</a>
<p>Si no querés continuar, no hace falta hacer nada — el acceso se cierra solo. Sin cobros.</p>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Hola, {name_safe}. Tu trial termina mañana ({ends_label}).\n\n"
            f"Con la suscripción se agregan: Mapa Astral Completo, Guía del Mes y previsión de la semana.\n\n"
            f"Activar: {subscribe_url}\n\n"
            f"Si no querés continuar, no hace falta hacer nada.\n\n"
            f"¿Dudas? contato@astrodicas.pnzdigital.com.br"
        )
    else:
        subject = "Seu acesso grátis termina amanhã — continue no Diário Astral"
        content = f"""<h2>Olá, {name_safe}. Seu trial termina amanhã.</h2>
<p>Seu acesso grátis ao Diário Astral vence em <strong>{ends_label}</strong>.</p>
<p>Se quiser continuar recebendo seu horóscopo diário e desbloquear o restante:</p>
<div class="info-box">
    <strong>Com seus 30 dias você ganha:</strong><br>
    ✦ Mapa Astral Completo em PDF (uma vez, seu para sempre)<br>
    ✦ Guia do Mês<br>
    ✦ Previsão personalizada da semana<br>
    <br>
    É compra única de 30 dias: não fica cartão salvo nem renova sozinho.
</div>
<a href="{url_safe}" class="cta-button">Entrar no Diário Astral →</a>
<p>Se não quiser continuar, não precisa fazer nada — o acesso fecha sozinho. Sem cobranças.</p>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Olá, {name_safe}. Seu trial termina amanhã ({ends_label}).\n\n"
            f"Com a assinatura você ganha: Mapa Astral Completo em PDF, Guia do Mês e previsão da semana.\n\n"
            f"Assinar: {subscribe_url}\n\n"
            f"Se não quiser continuar, não precisa fazer nada.\n\n"
            f"Dúvidas: contato@astrodicas.pnzdigital.com.br"
        )

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_renewal_reminder_email(
    email: str,
    name: str,
    expires_at: "datetime",
    renewal_url: str,
    reminder_type: str = "7d",
) -> dict[str, Any]:
    """Avisa que o Diário Astral vai vencer. reminder_type: '7d' ou 'today'."""
    name_safe = _escape_html(name)
    expires_label = expires_at.strftime("%d/%m/%Y")
    renewal_url_safe = _escape_html(renewal_url)

    if reminder_type == "today":
        subject = "Seu Diário Astral vence hoje — renove para continuar"
        headline = "Seu acesso vence hoje."
        body_line = "Para continuar recebendo seu horóscopo diário e suas leituras astrológicas, renove seu Diário Astral agora."
        button_text = "Renovar agora"
    else:
        subject = "Seu Diário Astral vence em 7 dias — renove para não perder o acesso"
        headline = "Seu acesso vence em breve."
        body_line = f"Seu Diário Astral está ativo até <strong>{expires_label}</strong>. Renove antes do prazo e mantenha seu acesso sem interrupção."
        button_text = "Renovar Diário Astral"

    content = f"""<h2>{headline}</h2>
<p>Olá, {name_safe}!</p>
<p>{body_line}</p>
<div class="info-box">
    <strong>Diário Astral</strong> — acesso a horóscopo diário, guia mensal e leituras personalizadas.<br>
    <strong>Vencimento:</strong> {expires_label}
</div>
<a href="{renewal_url_safe}" class="cta-button">{button_text}</a>
<p>Se preferir não renovar, basta ignorar este e-mail. Sem cobranças automáticas.</p>
"""
    footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
    text_content = (
        f"{headline}\n\n"
        f"Olá, {name_safe}!\n\n"
        f"{body_line.replace('<strong>', '').replace('</strong>', '')}\n\n"
        f"Renovar: {renewal_url}\n\n"
        f"Se preferir não renovar, basta ignorar este e-mail. Sem cobranças automáticas.\n\n"
        f"Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
    )
    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_weekly_forecast_email(
    email: str,
    name: str,
    forecast_html: str,
    portal_url: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Envia a previsão da semana por e-mail, com o texto completo e link para o portal."""
    name_safe = _escape_html(name)
    portal_safe = _escape_html(portal_url)

    import re as _re
    forecast_plain = _re.sub(r"<[^>]+>", "", forecast_html).strip()

    if locale == "es-AR":
        subject = "Tu previsión de la semana — AstroDicas"
        content = f"""<h2>Hola, {name_safe}. Tu semana empieza aquí.</h2>
<p>La previsión astral de la semana que viene, calculada a partir de tu mapa natal:</p>
<div class="info-box" style="white-space:pre-line">{forecast_html}</div>
<a href="{portal_safe}" class="cta-button">Leer en el portal</a>
"""
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Hola, {name_safe}. Tu previsión astral de la semana que viene:\n\n"
            f"{forecast_plain}\n\nLeer en el portal: {portal_url}\n"
        )
    else:
        subject = "Sua previsão da semana — AstroDicas"
        content = f"""<h2>Olá, {name_safe}. Sua semana começa aqui.</h2>
<p>A previsão astral da próxima semana, calculada a partir do seu mapa natal:</p>
<div class="info-box" style="white-space:pre-line">{forecast_html}</div>
<a href="{portal_safe}" class="cta-button">Ler no portal</a>
"""
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        text_content = (
            f"Olá, {name_safe}. Sua previsão astral da próxima semana:\n\n"
            f"{forecast_plain}\n\nLer no portal: {portal_url}\n"
        )

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_winback_email(
    email: str,
    name: str,
    winback_url: str,
) -> dict[str, Any]:
    """Convida de volta quem não renovou, com oferta de preço menor."""
    name_safe = _escape_html(name)
    winback_url_safe = _escape_html(winback_url)

    subject = "Sentimos sua falta — volte ao AstroDicas com condição especial"
    content = f"""<h2>Olá, {name_safe}. Seu céu ainda tem muito a revelar.</h2>
<p>Seu Diário Astral venceu e sentimos sua falta. Preparamos uma condição especial para você voltar:</p>
<div class="info-box">
    <strong>Diário Astral</strong> — 30 dias de acesso<br>
    <strong>De R$ 27,90 por R$ 20,90</strong> nesta oferta
</div>
<p>Sem assinatura, sem compromisso. Pague uma vez e aproveite por 30 dias.</p>
<a href="{winback_url_safe}" class="cta-button">Quero voltar</a>
<p>Esta oferta é exclusiva e pode não estar disponível no site principal.</p>
"""
    footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
    text_content = (
        f"Olá, {name_safe}. Seu céu ainda tem muito a revelar.\n\n"
        f"Seu Diário Astral venceu e preparamos uma condição especial para você voltar:\n\n"
        f"Diário Astral — 30 dias de acesso por R$ 20,90 (de R$ 27,90)\n\n"
        f"Sem assinatura, sem compromisso.\n\n"
        f"Acessar oferta: {winback_url}\n\n"
        f"Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
    )
    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_coupon_winback_email(
    email: str,
    name: str,
    checkout_url: str,
    coupon_code: str,
    discount_pct: int,
    is_last_chance: bool,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Recuperação pós-vencimento com cupom de desconto.

    Para Argentina (locale es-AR) não há cupom nativo via Mercado Pago: o
    parâmetro coupon_code chega vazio e o e-mail usa a URL de oferta de saída
    com copy de 'precio especial' sem mencionar percentual — evita prometer
    desconto que o checkout MP não aplica.
    """
    name_safe = _escape_html(name)
    url_safe = _escape_html(checkout_url)
    has_coupon = bool(coupon_code)
    code_safe = _escape_html(coupon_code) if has_coupon else ""

    if locale == "es-AR":
        subject = (
            "Última oportunidad — volvé al Diario Astral con precio especial"
            if is_last_chance
            else "Te extrañamos — volvé al Diario Astral con precio especial"
        )
        offer_box = (
            "<strong>Diario Astral</strong> — 30 días de acceso<br>"
            "<strong>Precio especial exclusivo</strong> para clientes que volvieron"
        )
        cta_label = "Quiero volver →"
        body_intro = (
            "Es tu última oportunidad de volver al Diario Astral con condición especial."
            if is_last_chance
            else "Tu Diario Astral venció y te extrañamos. Preparamos una condición especial para que vuelvas:"
        )
        body_closing = "Sin suscripción, sin compromiso. Pagás una vez y disfrutás 30 días."
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        content = f"""<h2>Hola, {name_safe}. Tu cielo sigue esperándote.</h2>
<p>{body_intro}</p>
<div class="info-box">{offer_box}</div>
<p>{body_closing}</p>
<a href="{url_safe}" class="cta-button">{cta_label}</a>
<p>Esta oferta es exclusiva y puede no estar disponible en el sitio principal.</p>
"""
        text_content = (
            f"Hola, {name_safe}. Tu cielo sigue esperándote.\n\n"
            f"{body_intro}\n\n"
            f"Diario Astral — 30 días con precio especial\n\n"
            f"{body_closing}\n\n"
            f"Acceder: {checkout_url}\n\n"
            f"¿Dudas? contato@astrodicas.pnzdigital.com.br"
        )
    else:
        subject = (
            f"Última chance com {discount_pct}% off — volte ao Diário Astral"
            if is_last_chance
            else f"Sentimos sua falta — {discount_pct}% off para você voltar"
        )
        coupon_block = (
            f'<div class="info-box"><strong>Cupom: <code>{code_safe}</code></strong> — {discount_pct}% de desconto<br>'
            f"Aplique no checkout antes de finalizar a compra.</div>"
            if has_coupon
            else '<div class="info-box"><strong>Diário Astral</strong> — condição especial para você voltar</div>'
        )
        body_intro = (
            f"É sua última chance de voltar ao Diário Astral com {discount_pct}% de desconto."
            if is_last_chance
            else f"Seu Diário Astral venceu e sentimos sua falta. Use o cupom abaixo para voltar com {discount_pct}% off:"
        )
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        content = f"""<h2>Olá, {name_safe}. Seu céu ainda tem muito a revelar.</h2>
<p>{body_intro}</p>
{coupon_block}
<p>Sem assinatura, sem compromisso. Pague uma vez e aproveite por 30 dias.</p>
<a href="{url_safe}" class="cta-button">Quero voltar →</a>
<p>Desconto aplicado no checkout com o código acima. Oferta exclusiva.</p>
"""
        coupon_text = f"\nCupom: {coupon_code} ({discount_pct}% off — aplique no checkout)\n" if has_coupon else ""
        text_content = (
            f"Olá, {name_safe}. Seu céu ainda tem muito a revelar.\n\n"
            f"{body_intro}\n"
            f"{coupon_text}\n"
            f"Sem assinatura, sem compromisso.\n\n"
            f"Ir para o checkout: {checkout_url}\n\n"
            f"Dúvidas? contato@astrodicas.pnzdigital.com.br"
        )

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text_content)


def send_astro_alert_email(
    email: str,
    name: str,
    event_type: str,
    planet: str | None,
    portal_url: str,
    locale: str = "pt-BR",
) -> dict[str, Any]:
    """Aviso de lua nova, lua cheia ou início de retrogradação.

    event_type: 'lua_nova' | 'lua_cheia' | 'retrogrado'
    planet: nome do planeta (só para 'retrogrado'), ex: 'Mercúrio'
    """
    name_safe = _escape_html(name)
    portal_safe = _escape_html(portal_url)

    if locale == "es-AR":
        if event_type == "lua_nova":
            title = "🌑 Luna Nueva hoy"
            body = "<p>Hoy es <strong>Luna Nueva</strong> — el momento ideal para plantear intenciones y empezar proyectos. Tu mapa natal muestra cómo este ciclo te afecta personalmente.</p>"
            subject = "Luna Nueva hoy — AstroDicas"
            text = f"Hola, {name}. Hoy es Luna Nueva. Consultá tu portal para ver cómo te afecta."
        elif event_type == "lua_cheia":
            title = "🌕 Luna Llena hoy"
            body = "<p>Hoy es <strong>Luna Llena</strong> — energía de culminación, claridad y cierre de ciclos. Un buen momento para soltar lo que ya no sirve.</p>"
            subject = "Luna Llena hoy — AstroDicas"
            text = f"Hola, {name}. Hoy es Luna Llena. Consultá tu portal para ver cómo te afecta."
        else:
            planet_safe = _escape_html(planet or "")
            title = f"⟲ {planet_safe} retrógrado"
            body = f"<p><strong>{planet_safe}</strong> inicia su movimiento retrógrado hoy. Revisiones, reencuentros y pausas pueden surgir en las áreas que este planeta rige en tu carta.</p>"
            subject = f"{planet_safe} retrógrado — AstroDicas"
            text = f"Hola, {name}. {planet} está retrógrado a partir de hoy. Consultá tu portal."
        footer = "¿Dudas? Escribinos a contato@astrodicas.pnzdigital.com.br"
        content = f"""<h2>Hola, {name_safe}.</h2>
<h3>{title}</h3>
{body}
<a href="{portal_safe}" class="cta-button">Ver en el portal</a>
"""
    else:
        if event_type == "lua_nova":
            title = "🌑 Lua Nova hoje"
            body = "<p>Hoje é <strong>Lua Nova</strong> — o momento ideal para plantar intenções e iniciar projetos. Seu mapa natal mostra como esse ciclo afeta você pessoalmente.</p>"
            subject = "Lua Nova hoje — AstroDicas"
            text = f"Olá, {name}. Hoje é Lua Nova. Acesse seu portal para ver como isso te afeta."
        elif event_type == "lua_cheia":
            title = "🌕 Lua Cheia hoje"
            body = "<p>Hoje é <strong>Lua Cheia</strong> — energia de culminação, clareza e fechamento de ciclos. Um bom momento para soltar o que não serve mais.</p>"
            subject = "Lua Cheia hoje — AstroDicas"
            text = f"Olá, {name}. Hoje é Lua Cheia. Acesse seu portal para ver como isso te afeta."
        else:
            planet_safe = _escape_html(planet or "")
            title = f"⟲ {planet_safe} retrógrado"
            body = f"<p><strong>{planet_safe}</strong> inicia seu movimento retrógrado hoje. Revisões, reencontros e pausas podem surgir nas áreas que esse planeta rege no seu mapa.</p>"
            subject = f"{planet_safe} retrógrado — AstroDicas"
            text = f"Olá, {name}. {planet} entra em retrógrado hoje. Acesse seu portal."
        footer = "Dúvidas? Nos escreva em contato@astrodicas.pnzdigital.com.br"
        content = f"""<h2>Olá, {name_safe}.</h2>
<h3>{title}</h3>
{body}
<a href="{portal_safe}" class="cta-button">Acessar portal</a>
"""

    html_content = _html_template(content, footer)
    return _send_email(email, subject, html_content, text)
