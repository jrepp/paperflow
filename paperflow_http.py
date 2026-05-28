from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

DEFAULT_USER_AGENT = "paperflow/0.1.0"


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 2,
) -> object:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
        **(headers or {}),
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = exc.headers.get("Retry-After")
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            time.sleep(delay)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(2 ** attempt)
    if last_error:
        raise last_error
    raise RuntimeError("request failed without an error")
