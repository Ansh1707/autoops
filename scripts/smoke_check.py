import json
import os
import sys
import time
import urllib.error
import urllib.request


API_BASE = os.getenv("AUTOOPS_API_BASE", "http://localhost:8000").rstrip("/")


def request_json(path: str, method: str = "GET", body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def check(name: str, fn) -> dict:
    started = time.monotonic()
    try:
        data = fn()
        return {
            "name": name,
            "ok": True,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "detail": data,
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {
            "name": name,
            "ok": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "detail": str(exc),
        }


def main() -> int:
    results: list[dict] = []
    token_holder: dict[str, str] = {}

    results.append(check("health", lambda: request_json("/health")))
    results.append(check("ready", lambda: request_json("/ready")))
    results.append(check("metrics", lambda: request_json("/metrics")))
    results.append(check("preflight", lambda: request_json("/preflight")))

    def login():
        data = request_json("/token", method="POST", body={"username": "admin", "password": "password"})
        token_holder["token"] = data["access_token"]
        return {"token_type": data.get("token_type")}

    results.append(check("login", login))

    def submit_job():
        token = token_holder.get("token")
        if not token:
            raise ValueError("no token from login")
        return request_json(
            "/investigate",
            method="POST",
            body={"goal": "Run a smoke check: report OK only."},
            token=token,
        )

    results.append(check("submit_job", submit_job))

    summary = {
        "api_base": API_BASE,
        "ok": all(result["ok"] for result in results),
        "checks": results,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
