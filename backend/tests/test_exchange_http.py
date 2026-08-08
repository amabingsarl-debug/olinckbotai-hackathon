import httpx
import pytest

from app.exchanges.http import request_public_json


@pytest.mark.asyncio
async def test_public_request_retries_transient_server_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request, json={"error": "temporary"})
        return httpx.Response(200, request=request, json={"price": "100"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await request_public_json(client, "GET", "https://exchange.test/ticker", base_delay_seconds=0)
    assert result == {"price": "100"}
    assert attempts == 3


@pytest.mark.asyncio
async def test_public_request_retries_rate_limit():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status = 429 if attempts == 1 else 200
        return httpx.Response(status, request=request, json={"ok": status == 200})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await request_public_json(client, "GET", "https://exchange.test/candles", base_delay_seconds=0)
    assert result == {"ok": True}
    assert attempts == 2


@pytest.mark.asyncio
async def test_public_request_does_not_retry_client_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, request=request, json={"error": "bad request"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_public_json(client, "GET", "https://exchange.test/candles", base_delay_seconds=0)
    assert attempts == 1
