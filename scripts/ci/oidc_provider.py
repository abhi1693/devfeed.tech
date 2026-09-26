"""Local OIDC provider for browser CI; the application auth/session code stays real."""

import hashlib
import json
import secrets
import threading
import time
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


@contextmanager
def oidc_provider(web_origin):
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public.update(kid="ci-key", use="sig", alg="RS256")
    codes = {}
    exchanges = []
    callback = web_origin + "/api/v1/user/auth/callback"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/.well-known/openid-configuration":
                self.respond(
                    {
                        "issuer": issuer,
                        "authorization_endpoint": issuer + "/authorize",
                        "token_endpoint": issuer + "/token",
                        "jwks_uri": issuer + "/keys",
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none"],
                    }
                )
            elif url.path == "/keys":
                self.respond({"keys": [public]})
            elif url.path == "/authorize":
                params = parse_qs(url.query)
                if (
                    params.get("redirect_uri") != [callback]
                    or params.get("client_id") != ["browser-ci"]
                    or params.get("code_challenge_method") != ["S256"]
                    or not all(params.get(key) for key in ("state", "nonce", "code_challenge"))
                ):
                    self.respond({"error": "invalid_request"}, 400)
                    return
                code = secrets.token_urlsafe(32)
                codes[code] = params
                self.send_response(302)
                self.send_header(
                    "Location",
                    callback
                    + "?"
                    + urlencode(
                        {
                            "code": code,
                            "state": params["state"][0],
                            "iss": issuer,
                        }
                    ),
                )
                self.end_headers()
            else:
                self.respond({"error": "not_found"}, 404)

        def do_POST(self):
            if self.path != "/token":
                self.respond({"error": "not_found"}, 404)
                return
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            params = codes.pop(form.get("code", [""])[0], None)
            challenge = (
                urlsafe_b64encode(
                    hashlib.sha256(form.get("code_verifier", [""])[0].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            if (
                params is None
                or params["code_challenge"] != [challenge]
                or form.get("redirect_uri") != [callback]
                or form.get("client_id") != ["browser-ci"]
                or form.get("grant_type") != ["authorization_code"]
            ):
                self.respond({"error": "invalid_grant"}, 400)
                return
            now = int(time.time())
            claims = {
                "iss": issuer,
                "aud": "browser-ci",
                "sub": "browser-ci-reader",
                "iat": now,
                "exp": now + 300,
                "auth_time": now,
                "nonce": params["nonce"][0],
                "name": "Browser reader",
                "email": "browser@example.invalid",
                "email_verified": True,
                "urn:zitadel:iam:user:resourceowner:id": "ci",
            }
            exchanges.append(True)
            self.respond(
                {
                    "id_token": jwt.encode(
                        claims, signing_key, algorithm="RS256", headers={"kid": "ci-key"}
                    ),
                    "access_token": secrets.token_urlsafe(32),
                    "token_type": "Bearer",
                    "expires_in": 300,
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    issuer = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield issuer, exchanges
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
