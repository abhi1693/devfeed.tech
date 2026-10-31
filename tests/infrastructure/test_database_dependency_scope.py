"""The supported function scope releases each API's session before response I/O."""

import importlib
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from devfeed_core import db
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_serializer
from starlette.requests import Request


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_public_get_snapshots_do_not_change_mutation_session_policy(monkeypatch, method):
    from devfeed_api import dependencies

    session = object()
    read = Mock(return_value=lambda: nullcontext(session))
    write = Mock(return_value=lambda: nullcontext(session))
    monkeypatch.setattr(dependencies, "read_session_factory", read)
    monkeypatch.setattr(dependencies, "session_factory", write)
    request = Request({"type": "http", "method": method})
    generator = dependencies.get_session(request)
    assert next(generator) is session and request.state.public_read_session is session
    generator.close()
    expected, other = (read, write) if method in {"GET", "HEAD"} else (write, read)
    expected.assert_called_once_with()
    other.assert_not_called()


@pytest.mark.parametrize("service", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
@pytest.mark.parametrize("outcome", ["success", "handler_error", "serialization_error"])
def test_database_dependency_closes_after_serialization_before_response(
    monkeypatch, service, outcome
):
    dependencies = importlib.import_module(f"{service}.dependencies")
    events = []

    class TrackedSession:
        closed = False

        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *_args):
            self.closed = True
            events.append("close")

    session = TrackedSession()
    monkeypatch.setattr(db, "get_engine", Mock())
    monkeypatch.setattr(db, "sessionmaker", lambda *_args, **_kwargs: lambda: session)

    class Payload(BaseModel):
        value: str

        @field_serializer("value")
        def serialize_value(self, value):
            assert not session.closed
            events.append("serialize")
            if outcome == "serialization_error":
                raise RuntimeError("Serialization failed")
            return value

    app = FastAPI()

    def background_work():
        assert session.closed
        events.append("background")

    @app.get("/probe", response_model=Payload)
    def probe(background: BackgroundTasks, value: dependencies.DB):
        assert value is session and not session.closed
        events.append("handler")
        if outcome == "handler_error":
            raise RuntimeError("Handler failed")
        background.add_task(background_work)
        return Payload(value="ready")

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"].startswith("http.response."):
                assert session.closed
                events.append(message["type"])
            await send(message)

        await app(scope, receive, observe_send)

    with TestClient(observe_response, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    assert response.status_code == (200 if outcome == "success" else 500)
    assert events[:2] == ["open", "handler"]
    assert events.count("close") == 1
    assert events.index("close") < events.index("http.response.start")
    if outcome == "success":
        assert response.json() == {"value": "ready"}
        assert events.index("serialize") < events.index("close")
        assert events[-1] == "background"
    elif outcome == "serialization_error":
        assert events.index("serialize") < events.index("close")
