"""Regresiones de TLS y del arranque de la API usada por Vercel."""
import os
import ssl
import subprocess
import sys
import textwrap
from types import SimpleNamespace

from app import database


def test_remote_postgres_verifies_certificate_and_hostname(monkeypatch):
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(database_ssl=True))
    context = database.get_database_connect_args()["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_local_postgres_does_not_require_tls(monkeypatch):
    monkeypatch.setattr(database, "get_settings", lambda: SimpleNamespace(database_ssl=False))
    assert database.get_database_connect_args() == {}


def test_vercel_entrypoint_and_cors_boundary():
    environment = os.environ.copy()
    environment["ADMIN_CORS_ORIGINS"] = "http://localhost:5173"
    script = textwrap.dedent('''
        import asyncio
        from httpx import ASGITransport, AsyncClient
        from api.index import app
        from sqlalchemy.orm import configure_mappers

        configure_mappers()
        assert app.openapi()["paths"]

        async def check():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                health = await client.get("/health", headers={"Origin": "http://localhost:5173"})
                assert health.status_code == 200
                assert health.headers["access-control-allow-origin"] == "http://localhost:5173"
                request = {
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                }
                allowed = await client.options("/api/v1/admin/auth/login", headers=request)
                assert allowed.status_code == 200, allowed.text
                request["Origin"] = "https://sitio-no-autorizado.example"
                denied = await client.options("/api/v1/admin/auth/login", headers=request)
                assert denied.status_code == 400
                assert "access-control-allow-origin" not in denied.headers
                unauthenticated = await client.get("/api/v1/admin/auth/me")
                assert unauthenticated.status_code == 401

        asyncio.run(check())
    ''')
    result = subprocess.run([sys.executable, "-c", script], env=environment,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
