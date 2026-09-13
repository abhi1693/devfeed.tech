"""Verify native profiling after RQ's fork boundary and actual HTTP export."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

received = []


class Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if body:
            received.append((self.path.split("?", 1)[0], len(body)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
threading.Thread(target=server.serve_forever, daemon=True).start()
code = r"""
import os, time
from devfeed_core.config import Settings
from devfeed_core import telemetry
# RQ parents must not initialize native profiler threads.
pid = os.fork()
if pid == 0:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test@database.invalid/smoke_test",
        redis_url="redis://redis.invalid/15",
        pyroscope_server=os.environ["PROFILE_SMOKE_COLLECTOR"],
        metrics_enabled=False,
    )
    runtime = telemetry.Runtime("profile-smoke", settings)
    runtime.start(serve_metrics=False, profiling=True, tracing=False)
    if runtime.profiler is None:
        os._exit(2)
    objects = [bytearray(128_000) for _ in range(50)]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        sum(i*i for i in range(10000))
    runtime.close()
    os._exit(0)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0, status
"""
try:
    subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PROFILE_SMOKE_COLLECTOR": f"http://127.0.0.1:{server.server_port}"},
        check=True,
        timeout=20,
    )
    assert received, "Native profiler did not export a profile after fork/shutdown"
    print(f"Native forked profiler exported {len(received)} non-empty request(s)")
finally:
    server.shutdown()
    server.server_close()
