"""Provision the bundled Chimely through its authenticated public admin API."""

import hashlib
import hmac
import http.cookiejar
import json
import urllib.error
import urllib.request

from compose_dev import compose, configuration, write_env


def provision() -> None:
    services = configuration()["services"]
    service = services["chimely"]
    env = service["environment"]
    app = services["worker"]["environment"]
    if not all(env.get(key) for key in ("CHIMELY_ADMIN_EMAIL", "CHIMELY_ADMIN_PASSWORD")):
        raise RuntimeError("Run with --notifications to configure Chimely's dedicated credentials.")
    if len(env["CHIMELY_ADMIN_PASSWORD"]) < 12:
        raise RuntimeError("CHIMELY_ADMIN_PASSWORD must have at least 12 characters.")
    if env["CHIMELY_ADMIN_TLS_TERMINATED"] == "true":
        raise RuntimeError(
            "Automatic local provisioning requires CHIMELY_ADMIN_TLS_TERMINATED=false."
        )
    compose("up", "-d", "--wait", "chimely")
    port = service["ports"][0]
    host = port["host_ip"]
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    if ":" in host:
        host = f"[{host}]"
    origin = f"http://{host}:{port['published']}"
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def request(path, data=None, headers=None):
        request = urllib.request.Request(
            origin + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", "X-Chimely-Admin": "1", **(headers or {})},
        )
        try:
            with client.open(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Chimely setup request failed (HTTP {error.code})") from None
        except (OSError, ValueError):
            raise RuntimeError("Chimely setup connection or response failed") from None

    request(
        "/admin/api/login",
        {
            "email": env["CHIMELY_ADMIN_EMAIL"],
            "password": env["CHIMELY_ADMIN_PASSWORD"],
        },
    )
    slug = app.get("DEVFEED_CHIMELY_ADMIN_ENVIRONMENT") or "devfeed-admin"
    environments = request("/admin/api/environments")
    existing = next((item for item in environments if item["slug"] == slug), None)
    if existing:
        environment = request(f"/admin/api/environments/{existing['id']}")
        if not environment["require_subscriber_hash"]:
            raise RuntimeError(
                "The Chimely environment must have subscriber HMAC verification enabled."
            )
    else:
        environment = request(
            "/admin/api/environments",
            {
                "slug": slug,
                "name": "DevFeed administrators",
                "require_subscriber_hash": True,
            },
        )
    key = app.get("DEVFEED_CHIMELY_ADMIN_API_KEY")
    keys = request(f"/admin/api/environments/{environment['id']}/api-keys")
    # The public API cannot reveal existing plaintext keys. Prefix matching avoids
    # creating a new one on every rebuild; verify the key against a read endpoint.
    valid = False
    if key and any(key.startswith(item["key_prefix"]) and not item["revoked_at"] for item in keys):
        try:
            request(
                "/v1/subscribers/devfeed-compose-check/preferences",
                headers={
                    "Authorization": f"Bearer {key}",
                    "X-Chimely-Environment": slug,
                },
            )
            valid = True
        except RuntimeError as error:
            # A nonexistent subscriber still proves the management credential.
            if "HTTP 404" in str(error):
                valid = True
            elif "HTTP 401" not in str(error):
                raise
    if not valid:
        key = request(
            f"/admin/api/environments/{environment['id']}/api-keys",
            {
                "name": "DevFeed Compose delivery worker",
            },
        )["key"]
    secret = environment["subscriber_hmac_secret"]
    write_env(
        {
            "DEVFEED_CHIMELY_API_URL": "http://chimely:8080",
            "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": slug,
            "DEVFEED_CHIMELY_ADMIN_API_KEY": key,
            "DEVFEED_CHIMELY_ADMIN_HMAC_SECRET": secret,
        }
    )
    subscriber = "devfeed-compose-check"
    request(
        "/v1/inbox/counts",
        headers={
            "X-Chimely-Environment": slug,
            "X-Chimely-Subscriber": subscriber,
            "X-Chimely-Subscriber-Hash": hmac.new(
                secret.encode(), subscriber.encode(), hashlib.sha256
            ).hexdigest(),
        },
    )
    print("Chimely is ready; subscriber HMAC verified and credentials saved in .env.", flush=True)


if __name__ == "__main__":
    provision()
