import hashlib
import hmac
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "infra/chimely" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load("provision")
consumer = load("consumer")


class Chimely:
    def __init__(self):
        self.environments = {}
        self.keys = {}
        self.issued = 0
        self.counts = 0
        self.fail_counts = False

    def request(self, path, data=None, headers=None):
        if path == "/admin/api/login":
            return {}
        if path == "/admin/api/environments":
            if data is None:
                return list(self.environments.values())
            slug = data["slug"]
            assert slug not in self.environments
            value = {**data, "id": slug, "subscriber_hmac_secret": f"secret-{self.issued}"}
            self.environments[slug] = value
            self.keys[slug] = []
            return value
        if path.startswith("/admin/api/environments/"):
            slug = path.split("/")[4]
            if not path.endswith("/api-keys"):
                return self.environments[slug]
            if data is None:
                return self.keys[slug]
            self.issued += 1
            key = f"key-{self.issued:08}-private"
            self.keys[slug].append({"key": key, "key_prefix": key[:14], "revoked_at": None})
            return {"key": key}
        slug = headers["X-Chimely-Environment"]
        if path.startswith("/v1/subscribers/"):
            key = headers["Authorization"].removeprefix("Bearer ")
            if any(row["key"] == key and not row["revoked_at"] for row in self.keys[slug]):
                raise setup.SetupError("Missing subscriber", status=404)
            raise setup.SetupError("Invalid credential", status=401)
        assert path == "/v1/inbox/counts"
        if self.fail_counts:
            raise setup.SetupError("Transient failure", status=503)
        expected = hmac.new(
            self.environments[slug]["subscriber_hmac_secret"].encode(),
            headers["X-Chimely-Subscriber"].encode(),
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Chimely-Subscriber-Hash"] == expected
        self.counts += 1
        return {"unread": 0}


@pytest.fixture
def provisioner(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.os, "fchown", lambda *args: None)
    worker, admin = tmp_path / "worker/credentials.json", tmp_path / "admin/credentials.json"
    worker.parent.mkdir()
    admin.parent.mkdir()
    env = {
        "DEVFEED_NOTIFICATIONS_ENABLED": "true",
        "DEVFEED_CHIMELY_API_URL": setup.ORIGIN,
        "CHIMELY_ADMIN_EMAIL": "admin@example.test",
        "CHIMELY_ADMIN_PASSWORD": "test-password-long",
    }
    client = Chimely()

    def run():
        return setup.provision(env, client=client, worker_path=worker, admin_path=admin)

    return run, client, env, worker, admin


def test_first_start_and_repeated_start_reuse_credentials_with_separate_scopes(provisioner):
    run, client, env, worker, admin = provisioner
    assert run() == "ready"
    assert client.issued == 1 and client.counts == 1
    before = worker.stat().st_mtime_ns
    assert run() == "ready"
    assert client.issued == 1 and client.counts == 2
    assert worker.stat().st_mtime_ns == before
    assert worker.stat().st_mode & 0o777 == 0o640
    delivery = consumer.credentials("worker", env, worker)
    inbox = consumer.credentials("admin", env, admin)
    assert "DEVFEED_CHIMELY_ADMIN_HMAC_SECRET" not in delivery
    assert "DEVFEED_CHIMELY_ADMIN_API_KEY" not in inbox


def test_database_recreation_replaces_stale_keys_and_hmac_before_consumers_start(provisioner):
    run, client, env, worker, admin = provisioner
    run()
    old_worker, old_admin = setup.read_values(worker), setup.read_values(admin)
    env.update(old_worker)
    client.environments.clear()
    client.keys.clear()
    run()
    assert client.issued == 2
    assert consumer.credentials("worker", env, worker) != old_worker
    assert consumer.credentials("admin", env, admin) != old_admin


def test_revoked_key_is_replaced_and_existing_secure_environment_is_reused(provisioner):
    run, client, _, worker, _ = provisioner
    run()
    client.keys["devfeed-admin"][0]["revoked_at"] = "revoked"
    run()
    assert client.issued == 2 and len(client.environments) == 1
    assert setup.read_values(worker)["DEVFEED_CHIMELY_ADMIN_API_KEY"].startswith("key-00000002")


def test_interrupted_setup_preserves_issued_key_but_publishes_no_consumer_files(provisioner):
    run, client, _, worker, admin = provisioner
    client.fail_counts = True
    with pytest.raises(setup.SetupError):
        run()
    assert not worker.exists() and not admin.exists()
    assert client.issued == 1
    client.fail_counts = False
    run()
    assert client.issued == 1 and worker.exists() and admin.exists()


def test_separate_reader_environment_gets_separate_key_without_exposing_its_hmac(provisioner):
    run, client, env, worker, admin = provisioner
    env["DEVFEED_CHIMELY_USER_ENVIRONMENT"] = "devfeed-users"
    run()
    delivery = consumer.credentials("worker", env, worker)
    assert delivery["DEVFEED_CHIMELY_ADMIN_API_KEY"] != delivery["DEVFEED_CHIMELY_USER_API_KEY"]
    assert not any(key.endswith("HMAC_SECRET") for key in delivery)
    assert "DEVFEED_CHIMELY_USER_API_KEY" not in consumer.credentials("admin", env, admin)
    assert client.issued == 2


def test_insecure_existing_environment_is_not_used(provisioner):
    run, client, _, _, _ = provisioner
    run()
    client.environments["devfeed-admin"]["require_subscriber_hash"] = False
    with pytest.raises(setup.SetupError, match="HMAC"):
        run()
    assert client.issued == 1


@pytest.mark.parametrize(
    "change",
    [
        {"DEVFEED_NOTIFICATIONS_ENABLED": "false"},
        {"DEVFEED_CHIMELY_API_URL": "https://inbox.example"},
    ],
)
def test_external_and_disabled_integrations_need_no_local_credentials(provisioner, change):
    run, client, env, worker, admin = provisioner
    env.update(change)
    assert run() in {"external", "disabled"}
    assert consumer.credentials("worker", env, worker) == {}
    assert consumer.credentials("admin", env, admin) == {}
    assert client.issued == 0


def test_stale_and_overprivileged_consumer_files_fail_closed(provisioner):
    run, _, env, worker, admin = provisioner
    run()
    with pytest.raises(ValueError, match="scope"):
        consumer.credentials("admin", env, worker)
    env["DEVFEED_CHIMELY_ADMIN_ENVIRONMENT"] = "different"
    with pytest.raises(ValueError, match="configuration"):
        consumer.credentials("admin", env, admin)


def test_missing_bootstrap_credentials_fail_before_creating_anything(provisioner):
    run, client, env, worker, admin = provisioner
    env["CHIMELY_ADMIN_PASSWORD"] = ""
    with pytest.raises(setup.SetupError, match="CHIMELY_ADMIN_PASSWORD"):
        run()
    assert client.issued == 0 and not worker.exists() and not admin.exists()
