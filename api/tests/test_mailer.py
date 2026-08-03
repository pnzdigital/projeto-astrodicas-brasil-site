import json
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.mailer import (
    send_purchase_confirmation,
    send_reading_ready,
    send_welcome,
)


class MockHTTPResponse:
    """Mock HTTP response for urllib."""

    def __init__(self, data: dict, status: int = 200):
        self.data = json.dumps(data).encode("utf-8")
        self.status = status

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def mock_resend_key(monkeypatch):
    """Set up Resend API key for tests."""
    monkeypatch.setenv("RESEND_API_KEY", "test_key_123")
    monkeypatch.setenv("MAIL_FROM", "Test <test@example.com>")
    monkeypatch.setenv("PORTAL_URL", "https://portal.test/")


def test_send_welcome_pt_br(mock_resend_key, monkeypatch):
    """Test welcome email in Portuguese."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_123"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "João Silva", locale="pt-BR")

        assert result["sent"] is True
        assert result["id"] == "email_123"
        assert mock_urlopen.called

        # Verify request
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert body["to"] == "user@test.com"
        assert "Bem-vindo(a)" in body["subject"]
        assert "João Silva" in body["html"]
        assert "João Silva" in body["text"]
        assert "https://portal.test/" in body["html"]


def test_send_welcome_es_ar(mock_resend_key, monkeypatch):
    """Test welcome email in Spanish (Argentina)."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_456"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "María González", locale="es-AR")

        assert result["sent"] is True
        assert result["id"] == "email_456"

        # Verify request
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert body["to"] == "user@test.com"
        assert "¡Bienvenido" in body["subject"]
        assert "María González" in body["html"]
        assert "podés acceder" in body["html"]  # voseo


def test_send_purchase_confirmation_without_password(mock_resend_key, monkeypatch):
    """Test purchase confirmation without temp password."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_789"})
        mock_urlopen.return_value = mock_response

        result = send_purchase_confirmation(
            "buyer@test.com",
            "Ana Costa",
            "Plano Lua Premium",
            "R$ 99,90",
            locale="pt-BR",
        )

        assert result["sent"] is True
        assert result["id"] == "email_789"

        # Verify request
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert body["to"] == "buyer@test.com"
        assert "confirmada" in body["subject"]
        assert "Ana Costa" in body["html"]
        assert "Plano Lua Premium" in body["html"]
        assert "R$ 99,90" in body["html"]
        assert "senha temporária" not in body["html"].lower()


def test_send_purchase_confirmation_with_password(mock_resend_key, monkeypatch):
    """Test purchase confirmation with temp password."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_999"})
        mock_urlopen.return_value = mock_response

        result = send_purchase_confirmation(
            "buyer@test.com",
            "João da Silva",
            "Mapa Astral Completo",
            "R$ 199,90",
            locale="pt-BR",
            temp_password="SecurePass123!",
        )

        assert result["sent"] is True

        # Verify password in response
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert "SecurePass123!" in body["html"]
        assert "Senha temporária" in body["html"]


def test_send_purchase_confirmation_es_ar(mock_resend_key, monkeypatch):
    """Test purchase confirmation in Spanish."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_es1"})
        mock_urlopen.return_value = mock_response

        result = send_purchase_confirmation(
            "comprador@test.com",
            "Carlos Pérez",
            "Lectura Premium",
            "$199.90 ARS",
            locale="es-AR",
            temp_password="Password123!",
        )

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert "¡Tu compra" in body["subject"]
        assert "Carlos Pérez" in body["html"]
        assert "Contraseña temporal" in body["html"]
        assert "Podés acceder" in body["html"]  # voseo


def test_send_reading_ready_pt_br(mock_resend_key, monkeypatch):
    """Test reading ready notification in Portuguese."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_read1"})
        mock_urlopen.return_value = mock_response

        result = send_reading_ready(
            "user@test.com",
            "Marina Costa",
            "Horóscopo Diário",
            locale="pt-BR",
        )

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert body["to"] == "user@test.com"
        assert "pronta" in body["subject"].lower()
        assert "Marina Costa" in body["html"]
        assert "Horóscopo Diário" in body["html"]


def test_send_reading_ready_es_ar(mock_resend_key, monkeypatch):
    """Test reading ready notification in Spanish."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_read2"})
        mock_urlopen.return_value = mock_response

        result = send_reading_ready(
            "usuario@test.com",
            "Isabel Martínez",
            "Lectura del Mes",
            locale="es-AR",
        )

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        assert "lista" in body["subject"].lower()
        assert "Isabel Martínez" in body["html"]
        assert "Lectura del Mes" in body["html"]


def test_html_escaping(mock_resend_key, monkeypatch):
    """Test that HTML is escaped in user input."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_esc1"})
        mock_urlopen.return_value = mock_response

        result = send_welcome(
            "user@test.com",
            "<script>alert('xss')</script>",
            locale="pt-BR",
        )

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        # Script tags should be escaped
        assert "<script>" not in body["html"]
        assert "&lt;script&gt;" in body["html"]
        assert "&lt;/script&gt;" in body["html"]


def test_html_escaping_in_product_title(mock_resend_key, monkeypatch):
    """Test escaping in product title."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_esc2"})
        mock_urlopen.return_value = mock_response

        result = send_purchase_confirmation(
            "user@test.com",
            "User",
            'Plano "Premium" & Especial',
            "R$ 99,90",
            locale="pt-BR",
        )

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        # Quotes and ampersand should be escaped
        assert 'Plano "Premium" & Especial' not in body["html"]
        assert "&quot;" in body["html"]
        assert "&amp;" in body["html"]


def test_default_sender_is_naoresponda(monkeypatch):
    """Without MAIL_FROM set, the default sender must be naoresponda@pnzdigital.com.br."""
    monkeypatch.setenv("RESEND_API_KEY", "test_key_123")
    monkeypatch.delenv("MAIL_FROM", raising=False)

    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_default_from"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "Test User", locale="pt-BR")

        assert result["sent"] is True
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["from"] == "AstroDicas <naoresponda@pnzdigital.com.br>"


def test_no_api_key(monkeypatch):
    """Test that missing API key returns error without network call."""
    monkeypatch.setenv("RESEND_API_KEY", "")

    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        result = send_welcome("user@test.com", "Test User", locale="pt-BR")

        assert result["sent"] is False
        assert "RESEND_API_KEY ausente" in result["error"]
        # Should not attempt network call
        assert not mock_urlopen.called


def test_network_error(mock_resend_key, monkeypatch):
    """Test handling of network errors."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = send_welcome("user@test.com", "Test User", locale="pt-BR")

        assert result["sent"] is False
        assert "Network error" in result["error"]


def test_http_error(mock_resend_key, monkeypatch):
    """Test handling of HTTP errors."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        import urllib.error

        error = urllib.error.HTTPError(
            "https://api.resend.com/emails",
            401,
            "Unauthorized",
            {},
            BytesIO(b'{"message": "Invalid API key"}'),
        )
        mock_urlopen.side_effect = error

        result = send_welcome("user@test.com", "Test User", locale="pt-BR")

        assert result["sent"] is False
        assert "HTTP 401" in result["error"]


def test_unexpected_exception_is_caught(mock_resend_key, monkeypatch):
    """Erro inesperado (não HTTPError/URLError) nunca deve derrubar o request."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = ValueError("boom")

        result = send_welcome("user@test.com", "Test User", locale="pt-BR")

        assert result["sent"] is False
        assert "boom" in result["error"]


def test_authorization_header(mock_resend_key, monkeypatch):
    """Test that Authorization header is set correctly."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_xyz")

    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_auth"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "Test User")

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]

        assert request.headers["Authorization"] == "Bearer re_test_key_xyz"
        assert request.headers["Content-type"] == "application/json"


def test_timeout_respected(mock_resend_key, monkeypatch):
    """Test that timeout parameter is passed to urlopen."""
    monkeypatch.setenv("RESEND_TIMEOUT_SECONDS", "30")

    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_timeout"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "Test User")

        assert result["sent"] is True

        # Check timeout was passed
        call_args = mock_urlopen.call_args
        assert call_args[1]["timeout"] == 30


def test_branding_elements_in_html(mock_resend_key, monkeypatch):
    """Test that AstroDicas branding elements are in HTML."""
    with patch("app.mailer.urllib.request.urlopen") as mock_urlopen:
        mock_response = MockHTTPResponse({"id": "email_brand"})
        mock_urlopen.return_value = mock_response

        result = send_welcome("user@test.com", "Test User")

        assert result["sent"] is True

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))

        # Check for branding colors and elements
        assert "#080719" in body["html"]  # Dark background
        assert "#f4efe4" in body["html"]  # Light text
        assert "#d9ac5c" in body["html"]  # Gold accent
        assert "AstroDicas" in body["html"]
        assert "✨" in body["html"]
