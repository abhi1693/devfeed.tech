"""Exercise browser login and a durable profile write through the real services."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx
from oidc_provider import oidc_provider
from services import disposable_services
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports/live-browser"


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def process(command, env, directory, name):
    with (REPORTS / f"{name}.log").open("w") as log:
        child = subprocess.Popen(
            command,
            cwd=directory,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            yield child
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            except ProcessLookupError:
                pass


def wait_ready(url, child):
    for _ in range(90):
        if child.poll() is not None:
            raise RuntimeError(f"Service exited before becoming ready: {url}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Service did not become ready: {url}")


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        env = stack.enter_context(disposable_services())
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        web_port, user_port, public_port = port(), port(), port()
        web_origin = f"http://127.0.0.1:{web_port}"
        issuer, exchanges = stack.enter_context(oidc_provider(web_origin))
        env.update(
            DEVFEED_USER_BASE_URL=web_origin,
            DEVFEED_USER_API_URL=f"http://127.0.0.1:{user_port}",
            DEVFEED_PUBLIC_API_URL=f"http://127.0.0.1:{public_port}",
            DEVFEED_USER_COOKIE_SECURE="false",
            DEVFEED_USER_OIDC_ISSUER_URL=issuer,
            DEVFEED_USER_OIDC_CLIENT_ID="browser-ci",
            DEVFEED_USER_OIDC_ORGANIZATION_ID="ci",
            DEVFEED_ANALYTICS_ENABLED="false",
            NEXT_TELEMETRY_DISABLED="1",
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
            env=env,
            cwd=directory,
            check=True,
        )
        for name, module, api_port in (
            ("user-api", "devfeed_user_api.main:app", user_port),
            ("public-api", "devfeed_api.main:app", public_port),
        ):
            child = stack.enter_context(
                process(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        module,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(api_port),
                    ],
                    env,
                    directory,
                    name,
                )
            )
            wait_ready(f"http://127.0.0.1:{api_port}/health/ready", child)
        web = stack.enter_context(
            process(
                [
                    "node",
                    str(ROOT / "node_modules/next/dist/bin/next"),
                    "start",
                    str(ROOT / "apps/web"),
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                env,
                ROOT / "apps/web",
                "web",
            )
        )
        wait_ready(web_origin + "/login", web)
        subprocess.run(
            ["node", "scripts/testing/live-browser.mjs", web_origin],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )
        assert len(exchanges) == 1, "Browser must complete one real authorization-code exchange"
        engine = create_engine(env["DEVFEED_DATABASE_URL"])
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT profile FROM user_accounts WHERE subject = 'browser-ci-reader'")
                ).scalar_one()
                assert row["display_name"] == "CI persistent reader", row
        finally:
            engine.dispose()
        print("PASS: browser login, Redis session, CSRF-protected write and PostgreSQL persistence")


if __name__ == "__main__":
    main()
