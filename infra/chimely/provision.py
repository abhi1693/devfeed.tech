"""Provision the bundled inbox before its consumers start; never print credentials."""

import fcntl
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ORIGIN = "http://chimely:8080"
WORKER = Path("/credentials/worker/credentials.json")
ADMIN = Path("/credentials/admin/credentials.json")
USER = Path("/credentials/user/credentials.json")


class SetupError(Exception):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            NoRedirect(),
        )

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(
            ORIGIN + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", "X-Chimely-Admin": "1", **(headers or {})},
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise SetupError("Chimely setup response exceeded its size limit")
                return json.loads(body)
        except urllib.error.HTTPError as error:
            raise SetupError(
                f"Chimely setup request failed (HTTP {error.code})", status=error.code
            ) from None
        except (OSError, ValueError):
            raise SetupError("Chimely setup connection or response failed") from None


def read_values(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_values(path, values):
    """The app UID can read only its own mounted file; the provisioner can reuse it."""
    if read_values(path) == values:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(values, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o640)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), 10001, 0)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def valid_key(client, slug, key, keys):
    if not key or not any(
        key.startswith(item["key_prefix"]) and not item["revoked_at"] for item in keys
    ):
        return False
    try:
        client.request(
            "/v1/subscribers/devfeed-compose-check/preferences",
            headers={"Authorization": f"Bearer {key}", "X-Chimely-Environment": slug},
        )
        return True
    except SetupError as error:
        if error.status == 404:
            return True  # Missing subscriber, authenticated management credential.
        if error.status == 401:
            return False
        raise


def provision(env, *, client=None, worker_path=WORKER, admin_path=ADMIN, user_path=USER):
    if env.get("DEVFEED_NOTIFICATIONS_ENABLED", "false").lower() not in {"true", "1", "yes", "on"}:
        return "disabled"
    if env.get("DEVFEED_CHIMELY_API_URL", ORIGIN).rstrip("/") != ORIGIN:
        return "external"
    if not env.get("CHIMELY_ADMIN_EMAIL") or len(env.get("CHIMELY_ADMIN_PASSWORD", "")) < 12:
        raise SetupError(
            "Set CHIMELY_ADMIN_EMAIL and CHIMELY_ADMIN_PASSWORD (12+ characters) in .env"
        )
    if env.get("CHIMELY_ADMIN_TLS_TERMINATED", "false").lower() == "true":
        raise SetupError("Bundled provisioning requires CHIMELY_ADMIN_TLS_TERMINATED=false")
    slugs = {
        "admin": env.get("DEVFEED_CHIMELY_ADMIN_ENVIRONMENT") or "devfeed-admin",
        "user": env.get("DEVFEED_CHIMELY_USER_ENVIRONMENT") or "",
    }
    if any(slug and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug) for slug in slugs.values()):
        raise SetupError("Chimely environments must be valid slugs")
    if slugs["admin"] == slugs["user"]:
        raise SetupError("Admin and user notifications require separate environments")
    client = client or Client()
    with (worker_path.parent / ".provision.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        client.request(
            "/admin/api/login",
            {"email": env["CHIMELY_ADMIN_EMAIL"], "password": env["CHIMELY_ADMIN_PASSWORD"]},
        )
        state_path = worker_path.with_name("issued-keys.json")
        previous = read_values(state_path) or read_values(worker_path)
        worker = {
            "DEVFEED_CHIMELY_API_URL": ORIGIN,
            **{
                f"DEVFEED_CHIMELY_{audience.upper()}_ENVIRONMENT": slug
                for audience, slug in slugs.items()
            },
        }
        admin = dict(worker)
        user = dict(worker)
        environments = client.request("/admin/api/environments")
        for audience, slug in slugs.items():
            if not slug:
                continue
            existing = next((item for item in environments if item["slug"] == slug), None)
            environment = (
                client.request(f"/admin/api/environments/{existing['id']}")
                if existing
                else client.request(
                    "/admin/api/environments",
                    {"slug": slug, "name": f"DevFeed {audience}", "require_subscriber_hash": True},
                )
            )
            if not environment["require_subscriber_hash"]:
                raise SetupError(
                    "The Chimely environment must require subscriber HMAC verification"
                )
            api_keys = f"/admin/api/environments/{environment['id']}/api-keys"
            keys = client.request(api_keys)
            field = f"DEVFEED_CHIMELY_{audience.upper()}_API_KEY"
            candidates = [env.get(field), previous.get(field)]
            key = next((key for key in candidates if valid_key(client, slug, key, keys)), None)
            if not key:
                key = client.request(api_keys, {"name": "DevFeed Compose delivery worker"})["key"]
            worker[field] = key
            # Persist issued keys before subsequent requests, so interrupted setup
            # reuses them. Consumers wait for this entire job to succeed.
            save_values(state_path, {**previous, **worker})
            secret = environment["subscriber_hmac_secret"]
            inbox = admin if audience == "admin" else user
            inbox[f"DEVFEED_CHIMELY_{audience.upper()}_HMAC_SECRET"] = secret
            subscriber = "devfeed-compose-check"
            client.request(
                "/v1/inbox/counts",
                headers={
                    "X-Chimely-Environment": slug,
                    "X-Chimely-Subscriber": subscriber,
                    "X-Chimely-Subscriber-Hash": hmac.new(
                        secret.encode(), subscriber.encode(), hashlib.sha256
                    ).hexdigest(),
                },
            )
        save_values(worker_path, worker)
        save_values(admin_path, admin)
        save_values(user_path, user)
    return "ready"


if __name__ == "__main__":
    try:
        print(f"Chimely provisioning: {provision(os.environ)}", flush=True)
    except SetupError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("Chimely provisioning failed; credentials were not logged", file=sys.stderr)
        sys.exit(1)
