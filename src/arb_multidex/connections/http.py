"""Bounded JSON transport used by the two public read adapters."""

import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TransportError(Exception):
    pass


def request_json(url, *, method="GET", body=None, timeout=5):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            data = response.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise TransportError("oversized JSON response")
        return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        exc.close()
        raise TransportError(f"HTTP {exc.code}") from exc
    except (OSError, ValueError, HTTPException) as exc:
        raise TransportError("public HTTP request failed") from exc
