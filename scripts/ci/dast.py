"""Scan CI-owned real applications with ZAP; never accept a supplied target or credentials."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from live_browser import port, process, wait_ready
from oidc_provider import oidc_provider
from services import disposable_services, docker
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports/dast"
ZAP_IMAGE = (
    "ghcr.io/zaproxy/zaproxy:2.17.0@"
    "sha256:781a2bdaea47324e7bab583e2263f21d257b0aee61ed51521a5be45f5f5081ef"
)
# Reflected XSS and non-time-based SQL injection; no OAST, file upload or destructive rules.
ACTIVE_RULES = "40012,40018"
ACTIVE_SECONDS = 480


def local_origin(value):
    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
    ):
        raise ValueError("DAST targets must be allocated HTTP loopback origins")
    return value


def safe_url(value, origins):
    url = urlsplit(value)
    origin = f"{url.scheme}://{url.netloc}"
    if origin not in origins:
        raise ValueError("ZAP recorded an out-of-scope alert")
    # Do not serialize query values, payloads, callback codes or browser session material.
    return origin + url.path


def findings(alerts, origins):
    results = []
    for alert in alerts:
        risk = (
            int(alert["riskcode"])
            if "riskcode" in alert
            else {"Informational": 0, "Low": 1, "Medium": 2, "High": 3}[alert["risk"]]
        )
        if risk not in range(4) or not str(alert["pluginId"]).isdigit():
            raise ValueError("Malformed ZAP alert")
        results.append(
            {
                "rule": str(alert["pluginId"]),
                "risk": risk,
                "name": alert["name"],
                "url": safe_url(alert["url"], origins),
                "cwe": str(alert.get("cweid", "")),
                "wasc": str(alert.get("wascid", "")),
            }
        )
    return results


def completed_rules(progress):
    # ZAP 2.17 serializes plugin progress as seven values rather than an object.
    # Validate the complete vendor record before giving its fields names.
    fields = ("name", "id", "quality", "status", "milliseconds", "requests", "alerts")
    rules = []
    for host in progress:
        if not isinstance(host, dict) or "HostProcess" not in host:
            continue
        for plugin in host["HostProcess"]:
            values = plugin["Plugin"]
            if not isinstance(values, list) or len(values) != len(fields):
                raise RuntimeError("ZAP plugin progress has an unsupported format")
            rule = dict(zip(fields, values, strict=True))
            if rule["id"] in ACTIVE_RULES.split(","):
                if rule["status"] != "Complete" or int(rule["requests"]) <= 0:
                    raise RuntimeError(
                        "Active scan rule was skipped, stopped or did not send attacks"
                    )
                rules.append({"id": rule["id"], "requests": int(rule["requests"])})
    if sorted(rule["id"] for rule in rules) != sorted(ACTIVE_RULES.split(",")):
        raise RuntimeError("Active scan did not complete every selected rule")
    return rules


class Zap:
    def __init__(self, origin, key):
        self.origin = origin
        self.key = key
        self.client = httpx.Client(timeout=30, trust_env=False)

    def call(self, component, kind, operation, **params):
        response = self.client.get(
            f"{self.origin}/JSON/{component}/{kind}/{operation}/",
            params={"apikey": self.key, **params},
        )
        if response.is_error:
            raise RuntimeError(f"ZAP HTTP {response.status_code}: {component}/{operation}")
        result = response.json()
        if "code" in result or result.get("Result", "OK") != "OK":
            raise RuntimeError(f"ZAP API failed: {component}/{operation}")
        return result

    def wait(self, ready, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if ready():
                return
            time.sleep(1)
        raise RuntimeError("ZAP scan did not complete within its budget")

    def passive_wait(self):
        self.wait(
            lambda: int(self.call("pscan", "view", "recordsToScan")["recordsToScan"]) == 0, 120
        )

    def scope(self, origins):
        context = self.call("context", "action", "newContext", contextName="DevFeed")["contextId"]
        for origin in origins:
            local_origin(origin)
            self.call(
                "context",
                "action",
                "includeInContext",
                contextName="DevFeed",
                regex=re.escape(origin) + r"/.*",
            )
        self.call(
            "context", "action", "setContextInScope", contextName="DevFeed", booleanInScope="true"
        )
        self.call("pscan", "action", "setScanOnlyInScope", onlyInScope="true")
        return context

    def cookie(self, origin, value):
        local_origin(origin)
        self.call(
            "replacer",
            "action",
            "addRule",
            description=f"Session for {origin}",
            enabled="true",
            matchType="REQ_HEADER",
            matchRegex="false",
            matchString="Cookie",
            replacement=value,
            url=re.escape(origin) + r"/.*",
        )

    def active(self, targets, context):
        if not targets:
            raise ValueError("Active scans require explicit targets")
        self.call("ascan", "action", "addScanPolicy", scanPolicyName="Bounded")
        self.call("ascan", "action", "disableAllScanners", scanPolicyName="Bounded")
        self.call("ascan", "action", "enableScanners", ids=ACTIVE_RULES, scanPolicyName="Bounded")
        self.call(
            "ascan",
            "action",
            "setScannerAttackStrength",
            id=ACTIVE_RULES.split(",")[0],
            attackStrength="LOW",
            scanPolicyName="Bounded",
        )
        self.call(
            "ascan",
            "action",
            "setScannerAttackStrength",
            id=ACTIVE_RULES.split(",")[1],
            attackStrength="LOW",
            scanPolicyName="Bounded",
        )
        self.call("ascan", "action", "setOptionThreadPerHost", Integer="2")
        self.call("ascan", "action", "setOptionMaxRuleDurationInMins", Integer="1")
        self.call("ascan", "action", "setOptionMaxScanDurationInMins", Integer="2")
        self.call("ascan", "action", "setOptionDelayInMs", Integer="50")
        deadline = time.monotonic() + ACTIVE_SECONDS
        evidence = []
        for target in targets:
            scan = self.call(
                "ascan",
                "action",
                "scan",
                url=target,
                recurse="false",
                inScopeOnly="true",
                scanPolicyName="Bounded",
                method="GET",
                contextId=context,
            )["scan"]
            self.wait(
                lambda scan=scan: (
                    self.call("ascan", "view", "status", scanId=scan)["status"] == "100"
                ),
                max(0, deadline - time.monotonic()),
            )
            # Message IDs prove that attacks ran, rather than just marking a scan done.
            messages = self.call("ascan", "view", "messagesIds", scanId=scan)["messagesIds"]
            if not messages:
                raise RuntimeError("Active scan completed without attack requests")
            rules = completed_rules(
                self.call("ascan", "view", "scanProgress", scanId=scan)["scanProgress"]
            )
            evidence.append(
                {"url": target.split("?", 1)[0], "requests": len(messages), "rules": rules}
            )
        return evidence


@contextmanager
def zap_server():
    origin = f"http://127.0.0.1:{port()}"
    key = secrets.token_urlsafe(32)
    container = docker(
        "run",
        "-d",
        "--rm",
        "--no-healthcheck",
        "--network",
        "host",
        "--cpus",
        "2",
        "--memory",
        "2g",
        ZAP_IMAGE,
        "zap.sh",
        "-daemon",
        "-host",
        "127.0.0.1",
        "-port",
        str(urlsplit(origin).port),
        "-config",
        f"api.key={key}",
        "-config",
        "autoupdate.checkOnStart=false",
        "-config",
        "autoupdate.downloadNewRelease=false",
        "-config",
        "connection.timeoutInSecs=10",
    )
    zap = Zap(origin, key)
    try:
        for _ in range(120):
            try:
                if zap.call("core", "view", "version")["version"] == "2.17.0":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Pinned ZAP did not become ready")
        yield zap
    finally:
        zap.client.close()
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, capture_output=True, timeout=30
        )


def seed(env):
    from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic

    engine = create_engine(env["DEVFEED_DATABASE_URL"])
    try:
        with Session(engine) as session, session.begin():
            source = Source(
                name="CI publisher",
                slug="ci-publisher",
                source_type="publisher",
                feed_url="https://publisher.invalid/feed",
                approval_status="approved",
                website_url="https://publisher.invalid",
            )
            topic = Topic(name="CI security", slug="ci-security", kind="concept", status="active")
            article = Article(
                id=uuid.UUID(int=1),
                canonical_url="https://publisher.invalid/security",
                url_hash="a" * 64,
                title="CI security article",
                slug="ci-security-article",
                summary="A populated disposable scan fixture",
                language="en",
                review_status="approved",
                publication_status="published",
            )
            session.add_all([source, topic, article])
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key="ci-security",
                    original_url=article.canonical_url,
                )
            )
            session.add(
                ArticleTopic(
                    article_id=article.id,
                    topic_id=topic.id,
                    role="primary",
                    relevance=1,
                    evidence="CI",
                    origin="manual",
                )
            )
            return str(article.id)
    finally:
        engine.dispose()


def stack_apps(stack, directory, skip_build):
    env = stack.enter_context(disposable_services())
    # Backend processes run outside the repo's .env directory. Next also reads
    # app-local dotenv files; blank their keys before adding CI-owned settings.
    env = {
        key: value
        for key, value in env.items()
        if key.startswith("DEVFEED_") or key in {"PATH", "HOME", "USER", "LANG", "CI"}
    }
    for app in ("web", "admin"):
        for path in (ROOT / "apps" / app).glob(".env*"):
            if path.name != ".env.example":
                for key in dotenv_values(path):
                    if key not in env:
                        env[key] = ""
    origins = {
        name: f"http://127.0.0.1:{port()}"
        for name in ("web", "admin", "public", "user", "admin_api")
    }
    reader_issuer, reader_exchanges = stack.enter_context(
        oidc_provider(origins["web"], client_id="dast-reader", token_ttl=3600)
    )
    admin_issuer, admin_exchanges = stack.enter_context(
        oidc_provider(
            origins["admin"],
            namespace="admin",
            client_id="dast-admin",
            roles=("superuser",),
            token_ttl=3600,
        )
    )
    env.update(
        DEVFEED_USER_BASE_URL=origins["web"],
        DEVFEED_ADMIN_BASE_URL=origins["admin"],
        DEVFEED_USER_API_URL=origins["user"],
        DEVFEED_ADMIN_API_URL=origins["admin_api"],
        DEVFEED_PUBLIC_API_URL=origins["public"],
        DEVFEED_USER_COOKIE_SECURE="false",
        DEVFEED_ADMIN_COOKIE_SECURE="false",
        DEVFEED_ANALYTICS_ENABLED="false",
        DEVFEED_USER_OIDC_ISSUER_URL=reader_issuer,
        DEVFEED_USER_OIDC_CLIENT_ID="dast-reader",
        DEVFEED_USER_OIDC_ORGANIZATION_ID="ci",
        DEVFEED_OIDC_ISSUER_URL=admin_issuer,
        DEVFEED_OIDC_CLIENT_ID="dast-admin",
        DEVFEED_OIDC_ORGANIZATION_ID="ci",
        NEXT_TELEMETRY_DISABLED="1",
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        env=env,
        cwd=directory,
        timeout=120,
    )
    article = seed(env)
    for name, module in (
        ("public", "devfeed_api"),
        ("user", "devfeed_user_api"),
        ("admin_api", "devfeed_admin_api"),
    ):
        child = stack.enter_context(
            process(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    f"{module}.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(urlsplit(origins[name]).port),
                    "--no-access-log",
                ],
                env,
                directory,
                name,
                log_directory=directory,
            )
        )
        wait_ready(origins[name] + "/health/ready", child)
    for name in ("web", "admin"):
        if not skip_build:
            subprocess.run(
                ["npm", "run", f"{name}:build"], check=True, env=env, cwd=ROOT, timeout=300
            )
        child = stack.enter_context(
            process(
                [
                    "node",
                    str(ROOT / "node_modules/next/dist/bin/next"),
                    "start",
                    str(ROOT / "apps" / name),
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(urlsplit(origins[name]).port),
                ],
                env,
                ROOT / "apps" / name,
                name,
                log_directory=directory,
            )
        )
        wait_ready(origins[name] + "/login", child)
    return env, origins, (reader_issuer, admin_issuer), (reader_exchanges, admin_exchanges), article


def scan(mode, skip_build=False):
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="devfeed-dast-")))
        env, origins, issuers, exchanges, article = stack_apps(stack, directory, skip_build)
        zap = stack.enter_context(zap_server())
        context = zap.scope(origins.values())
        settings = directory / "browser.json"
        sessions_path = directory / "sessions.json"
        settings.write_text(
            json.dumps(
                {
                    **origins,
                    "proxy": zap.origin,
                    "sessions": str(sessions_path),
                    "article": article,
                    "origins": [*origins.values(), *issuers],
                }
            )
        )
        subprocess.run(
            ["node", "scripts/testing/dast-browser.mjs", str(settings)],
            check=True,
            env=env,
            cwd=ROOT,
            timeout=240,
        )
        if any(len(values) != 1 for values in exchanges):
            raise RuntimeError("Both real authorization-code exchanges must complete exactly once")
        sessions = json.loads(sessions_path.read_text())
        # Probe directly without the scanner's authenticated header replacement.
        # The public process and each gateway must preserve namespace boundaries.
        with httpx.Client(timeout=30, trust_env=False) as boundary:
            for origin, path, expected, headers in (
                (origins["public"], "/v1/admin/users", 404, {}),
                (origins["public"], "/v1/user/bookmarks", 404, {}),
                (origins["web"], "/api/v1/admin/users", 404, {}),
                (origins["admin_api"], "/v1/admin/users", 401, {}),
                (
                    origins["admin_api"],
                    "/v1/admin/users",
                    401,
                    {"Cookie": sessions["reader"]["cookie"]},
                ),
            ):
                if boundary.get(origin + path, headers=headers).status_code != expected:
                    raise RuntimeError("Assembled application crossed an authentication boundary")
        for name, origin in (("reader", origins["user"]), ("admin", origins["admin_api"])):
            zap.cookie(origin, sessions[name]["cookie"])

        with httpx.Client(proxy=zap.origin, timeout=30, trust_env=False) as client:

            def get(origin, path):
                response = client.get(origin + path)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Scan coverage request failed: {path} ({response.status_code})"
                    )
                return response.json()

            def verify_sessions():
                for origin, namespace, subject in (
                    (origins["user"], "user", "browser-ci-reader"),
                    (origins["admin_api"], "admin", "browser-ci-admin"),
                ):
                    identity = get(origin, f"/v1/{namespace}/auth/me")
                    if not identity or identity["subject"] != subject:
                        raise RuntimeError("Authenticated scan silently became anonymous")

            verify_sessions()
            profile = origins["web"] + "/api/v1/user/settings/profile"
            headers = {"Cookie": sessions["reader"]["cookie"], "Origin": origins["web"]}
            denied = client.put(profile, headers=headers, json={"display_name": "Denied"})
            if denied.status_code != 403:
                raise RuntimeError("Reader write without CSRF was accepted")
            headers["X-CSRF-Token"] = sessions["reader"]["csrf"]
            saved = client.put(profile, headers=headers, json={"display_name": "DAST reader"})
            if saved.status_code != 200:
                raise RuntimeError("Authenticated reader profile write failed")
            bookmarked = client.put(
                origins["web"] + f"/api/v1/user/articles/{article}/bookmark",
                headers=headers,
                json={"bookmarked": True},
            )
            if bookmarked.status_code != 200:
                raise RuntimeError("Authenticated bookmark write failed")
            targets = [
                origins["public"] + "/v1/sources?q=CI&limit=2",
                origins["public"] + "/v1/topics?q=CI&limit=2",
                origins["user"] + "/v1/user/bookmarks?limit=2",
                origins["admin_api"] + "/v1/admin/articles?q=CI&limit=2",
                origins["admin_api"] + "/v1/admin/users?q=Browser&limit=2",
            ]
            for target in targets:
                response = client.get(target)
                if response.status_code != 200:
                    raise RuntimeError("Selected scan route did not respond successfully")
            feed = get(origins["public"], "/v1/feed?limit=2")
            if not any(row["id"] == article for row in feed["items"]):
                raise RuntimeError("Scan fixture must be publicly visible")
            active = zap.active(targets, context) if mode == "active" else []
            verify_sessions()
            if get(origins["user"], "/v1/user/settings/profile")["display_name"] != "DAST reader":
                raise RuntimeError("Authenticated profile write did not persist")
        zap.passive_wait()
        urls = zap.call("core", "view", "urls")["urls"]
        traffic = {
            name: sum(url.startswith(origin + "/") for url in urls)
            for name, origin in origins.items()
        }
        if any(count < 2 for count in traffic.values()):
            raise RuntimeError("ZAP did not observe every assembled service")
        alerts = zap.call("core", "view", "alerts", start="0", count="10000")["alerts"]
        if len(alerts) >= 10000:
            raise RuntimeError("ZAP alerts were truncated")
        return {
            "completed": True,
            "mode": mode,
            "image": ZAP_IMAGE,
            "traffic": traffic,
            "authenticated": ["reader", "admin"],
            "active": active,
            "findings": findings(alerts, set(origins.values())),
        }


def write_report(report, *, publish=True):
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    counts = {
        risk: sum(item["risk"] == risk for item in report.get("findings", [])) for risk in range(4)
    }
    passed = report["completed"] and counts[3] == 0
    lines = [
        f"### ZAP {report['mode']}: {'PASS' if passed else 'FAIL'}",
        "",
        f"High: {counts[3]} · Medium: {counts[2]} · Low: {counts[1]} · Info: {counts[0]}",
        "",
        "High-risk findings block releases. Medium and lower findings require review.",
        "HTTP loopback cookies deliberately omit Secure; deployed HTTPS cookies are not evaluated.",
        "",
    ]
    if report["completed"]:
        lines += [
            "Authenticated reader/admin sessions verified before and after scanning.",
            f"Observed URLs by service: {json.dumps(report['traffic'])}",
            f"Active attack requests: {sum(item['requests'] for item in report['active'])}",
            "",
        ]
        for item in report["findings"]:
            lines.append(
                f"- Risk {item['risk']} · rule {item['rule']} · {item['name']} · {item['url']}"
            )
    else:
        lines.append("Scan failed before complete coverage; inspect the failed job step.")
    summary = "\n".join(lines) + "\n"
    (REPORTS / "summary.md").write_text(summary)
    if publish and os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
            output.write(summary)
    if publish:
        print("\n".join(lines[:4]))
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("passive", "active"), default="passive")
    parser.add_argument(
        "--skip-build", action="store_true", help="Local reuse of existing production builds"
    )
    args = parser.parse_args()
    # Replace earlier results even if setup fails; stale success never counts as this run.
    report = {"completed": False, "mode": args.mode, "image": ZAP_IMAGE}
    write_report(report, publish=False)
    try:
        report = scan(args.mode, args.skip_build)
    finally:
        passed = write_report(report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
