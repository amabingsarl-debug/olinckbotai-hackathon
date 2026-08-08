import asyncio
from typing import Any

import httpx


TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


async def request_public_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.25,
    **kwargs,
) -> Any:
    attempts = max(1, max_attempts)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code not in TRANSIENT_STATUS_CODES:
                response.raise_for_status()
                return response.json()
            last_error = httpx.HTTPStatusError(
                f"transient exchange response: {response.status_code}",
                request=response.request,
                response=response,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            await asyncio.sleep(base_delay_seconds * (2**attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("public exchange request failed")
