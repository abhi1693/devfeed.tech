"""Exercise disposable profiling credentials without starting containers or tests."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def test_profile_database_uses_a_fresh_password_without_exposing_it(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts/profile-api.sh", scripts / "profile-api.sh"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.jsonl"
    for name, body in {
        "uv": """
if sys.argv[1:5] == ['run', '--locked', 'python', '-c']:
    os.execv(sys.executable, [sys.executable, *sys.argv[4:]])
if sys.argv[1:4] == ['run', '--locked', 'pytest']:
    record['database'] = os.environ['DEVFEED_TEST_DATABASE_URL']
""",
        "docker": """
if sys.argv[1] == 'run':
    if 'POSTGRES_PASSWORD' in sys.argv:
        record['password'] = os.environ['POSTGRES_PASSWORD']
        print('profile-postgres')
    else:
        print('profile-redis')
elif sys.argv[1] == 'exec' and sys.argv[-1] == 'ping':
    print('PONG')
elif sys.argv[1] == 'port':
    print('127.0.0.1:15432' if sys.argv[-1] == '5432/tcp' else '127.0.0.1:16379')
""",
    }.items():
        executable = binaries / name
        executable.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "record = {'tool': os.path.basename(sys.argv[0]), 'args': sys.argv[1:]}\n"
            + body
            + "\nwith open(os.environ['PROFILE_TEST_LOG'], 'a') as stream:\n"
            "    stream.write(json.dumps(record) + '\\n')\n"
        )
        executable.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.defpath}",
        "PROFILE_TEST_LOG": str(log),
        "DEVFEED_PROFILE_SUITE": "core",
    }
    passwords = []
    for _ in range(2):
        log.write_text("")
        result = subprocess.run(
            ["/bin/bash", str(scripts / "profile-api.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        records = [json.loads(line) for line in log.read_text().splitlines()]
        postgres = next(row for row in records if "password" in row)
        password = postgres["password"]
        assert len(password) == 64 and all(c in "0123456789abcdef" for c in password)
        assert password not in postgres["args"]
        test_run = next(row for row in records if "database" in row)
        target = urlsplit(test_run["database"])
        assert target.password == password
        assert (target.hostname, target.port, target.path) == (
            "127.0.0.1",
            15432,
            "/devfeed_profile_test",
        )
        assert password not in result.stdout + result.stderr
        removed = [row["args"] for row in records if row["args"][:2] == ["rm", "-f"]]
        assert removed == [["rm", "-f", "profile-postgres"], ["rm", "-f", "profile-redis"]]
        passwords.append(password)
    assert passwords[0] != passwords[1]
