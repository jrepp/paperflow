"""Small HTTP client shared by the generic API CLI and curator CLI."""
import json
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    pass


class APIClient:
    def __init__(self, base_url: str, timeout: float = 30):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("API URL must be an HTTP(S) service base URL")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.idempotency_key = None
        self.last_measurement = {}

    def request(self, method: str, path: str, body=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must start with a single slash")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, method=method.upper(), data=data,
            headers={"Accept": "application/json, text/markdown", "Content-Type": "application/json"})
        if self.idempotency_key and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
            request.add_header("Idempotency-Key", self.idempotency_key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                self.last_measurement = {"response_bytes": len(payload),
                    "db_queries": int(response.headers.get("X-Paperflow-DB-Queries", 0)),
                    "server_duration_ms": float(response.headers.get("X-Paperflow-Duration-Ms", 0))}
                text = payload.decode("utf-8")
                return json.loads(text) if "application/json" in response.headers.get("Content-Type", "") else text
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode("utf-8", errors="replace")
            raise APIError(f"HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError):
            raise APIError("Cannot reach Paperflow API; start 'paperflow serve' or check --api-url") from None
