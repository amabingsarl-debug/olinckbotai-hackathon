from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api import routes
from app.schemas.domain import UserCreate


@pytest.mark.asyncio
async def test_operator_secret_rejects_missing_or_wrong_value(monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(scheduler_secret="server-only"))
    with pytest.raises(HTTPException) as missing:
        await routes.require_operator_secret(None)
    with pytest.raises(HTTPException) as wrong:
        await routes.require_operator_secret("wrong")
    assert missing.value.status_code == 403
    assert wrong.value.status_code == 403
    await routes.require_operator_secret("server-only")


def test_every_sensitive_public_route_requires_operator_secret():
    protected_paths = {
        "/strategies/{name}",
        "/exchanges/{name}",
        "/risk",
        "/bot/start",
        "/bot/stop",
        "/backtests",
        "/ai/report",
    }
    found = set()
    for route in routes.router.routes:
        if route.path not in protected_paths or "GET" in route.methods:
            continue
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert routes.require_operator_secret in dependency_calls, route.path
        found.add(route.path)
    assert found == protected_paths


@pytest.mark.asyncio
async def test_public_registration_is_disabled_in_production(monkeypatch):
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(environment="production"),
    )
    with pytest.raises(HTTPException) as denied:
        await routes.register(UserCreate(email="blocked@example.com", password="password"), None)
    assert denied.value.status_code == 403
