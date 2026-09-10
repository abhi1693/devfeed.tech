"""Exercise real first-start, retained storage, and replaced-DB notification flows.

Creates a uniquely named disposable Compose project. Never reads the user's .env.
Only this test project's containers, networks and volumes are removed.
"""

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = (
    "python:3.12-alpine3.24@sha256:b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a"
)
PROBE = """import hashlib, hmac, json, os, sys, urllib.request, urllib.error
role = sys.argv[1]
origin = os.environ['DEVFEED_CHIMELY_API_URL']
slug = os.environ['DEVFEED_CHIMELY_ADMIN_ENVIRONMENT']
subscriber = 'disposable-compose-test'
def request(path, headers, data=None):
    req = urllib.request.Request(
        origin + path, headers={'Content-Type':'application/json', **headers},
        data=json.dumps(data).encode() if data else None)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)
if role == 'worker':
    assert not os.environ.get('DEVFEED_CHIMELY_ADMIN_HMAC_SECRET')
    key = os.environ['DEVFEED_CHIMELY_ADMIN_API_KEY']
    request('/v1/notifications', {'Authorization':'Bearer ' + key}, {
        'subscriber_id':subscriber, 'idempotency_key':'first-start-test',
        'category':'test.startup', 'payload':{'title':'Compose startup works'}
    })
else:
    assert not os.environ.get('DEVFEED_CHIMELY_ADMIN_API_KEY')
    headers = {'X-Chimely-Environment':slug, 'X-Chimely-Subscriber':subscriber,
               'X-Chimely-Subscriber-Hash':'0'*64}
    try:
        request('/v1/inbox/counts', headers)
        raise AssertionError('An invalid subscriber hash was accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    secret = os.environ['DEVFEED_CHIMELY_ADMIN_HMAC_SECRET']
    headers['X-Chimely-Subscriber-Hash'] = hmac.new(
        secret.encode(), subscriber.encode(), hashlib.sha256).hexdigest()
    result = request('/v1/inbox/items', headers)
    assert 'Compose startup works' in json.dumps(result)
print(role + ': notification round trip and credential scope passed', flush=True)
"""


def check():
    project = "devfeed-chimely-test-" + secrets.token_hex(5)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("DEVFEED_", "COMPOSE_", "POSTGRES_", "CHIMELY_", "CODEX_", "OPENAI_")
        )
    }
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        temp = Path(directory)
        env = temp / ".env"
        env.write_text(
            "\n".join(
                [
                    f"POSTGRES_PASSWORD={secrets.token_hex(32)}",
                    "DEVFEED_BIND_IP=127.0.0.1",
                    "CHIMELY_PORT=0",
                    "DEVFEED_NOTIFICATIONS_ENABLED=true",
                    "CHIMELY_ADMIN_EMAIL=admin@example.test",
                    f"CHIMELY_ADMIN_PASSWORD={secrets.token_hex(32)}",
                ]
            )
            + "\n"
        )
        env.chmod(0o600)
        # Docker's non-root probes need directory traversal, but the env stays private.
        temp.chmod(0o755)
        (temp / "probe.py").write_text(PROBE)
        consumers = {}
        for role in ("worker", "admin"):
            credential = "API_KEY" if role == "worker" else "HMAC_SECRET"
            consumers[f"{role}-probe"] = {
                "image": PYTHON,
                "user": "10001:10001",
                "read_only": True,
                "entrypoint": ["python", "/consumer.py", role],
                "command": ["python", "/probe.py", role],
                "environment": {
                    "DEVFEED_NOTIFICATIONS_ENABLED": "true",
                    "DEVFEED_CHIMELY_API_URL": "http://chimely:8080",
                    "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": "devfeed-admin",
                    # Stale env credentials must lose to the provisioned values.
                    f"DEVFEED_CHIMELY_ADMIN_{credential}": "stale-test-value",
                },
                "volumes": [
                    f"{ROOT}/infra/chimely/consumer.py:/consumer.py:ro",
                    f"{temp}/probe.py:/probe.py:ro",
                    f"chimely-{role}-credentials:/run/devfeed-chimely:ro",
                ],
                "depends_on": {
                    "chimely-provision": {"condition": "service_completed_successfully"},
                    **(
                        {"worker-probe": {"condition": "service_completed_successfully"}}
                        if role == "admin"
                        else {}
                    ),
                },
            }
        override = temp / "compose.json"
        override.write_text(json.dumps({"services": consumers}))
        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env),
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
        ]

        def compose(*args, capture=False):
            return subprocess.run(
                [*command, *args],
                env=environment,
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=capture,
                timeout=180,
            )

        def round_trip(label):
            compose("up", "-d", "admin-probe")
            compose("wait", "admin-probe")
            rows = compose("ps", "--all", "--format", "json", capture=True).stdout.splitlines()
            rows = [json.loads(row) for row in rows]
            for service in ("chimely-provision", "worker-probe", "admin-probe"):
                row = next(row for row in rows if row["Service"] == service)
                assert row["State"] == "exited" and row["ExitCode"] == 0, service
            state = (
                compose(
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "devfeed",
                    "-d",
                    "chimely",
                    "-At",
                    "-c",
                    "SELECT (SELECT count(*) FROM environments), (SELECT count(*) FROM api_keys), "
                    "(SELECT count(*) FROM notifications), (SELECT key_hash FROM api_keys LIMIT 1)",
                    capture=True,
                )
                .stdout.strip()
                .split("|")
            )
            assert state[:3] == ["1", "1", "1"], state[:3]
            print(
                f"{label}: delivery/inbox passed; one environment, key and notification",
                flush=True,
            )
            return state[3]

        try:
            first = round_trip("Fresh storage")
            compose("down", "--remove-orphans")
            assert round_trip("Retained storage restart") == first
            compose("down", "--remove-orphans")
            subprocess.run(
                ["docker", "volume", "rm", f"{project}_postgres-data"], check=True, timeout=30
            )
            assert round_trip("Replaced database with retained credentials") != first
        except Exception:
            compose("logs", "--tail", "30", "chimely-provision", "worker-probe", "admin-probe")
            raise
        finally:
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    check()
