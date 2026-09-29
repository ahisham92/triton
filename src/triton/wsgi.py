"""WSGI entry point, for hosts that run WSGI only (PythonAnywhere's web tab, or inside a Flask or
Django site).

Stand-alone:   ``application`` in the host's WSGI file (``from triton.wsgi import application``).
Inside a site: ``mount(site, "/triton", guarded(is_signed_in))`` puts Triton under /triton of the host
site, behind that site's sign-in (see the README, "Online on PythonAnywhere").


Django: check the session the same way Django does::

    from django.contrib.auth import get_user
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.core.handlers.wsgi import WSGIRequest

    def signed_in(environ):
        request = WSGIRequest(dict(environ))
        SessionMiddleware(lambda r: None).process_request(request)
        return get_user(request).is_authenticated

    application = mount(get_wsgi_application(), "/triton", guarded(signed_in, "/accounts/login/"))
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import Any

import anyio.to_thread

from .api import app

Environ = dict[str, Any]
StartResponse = Callable[..., Any]

CHUNK = 1 << 16


async def _inline(func: Callable, *args: Any, **_: Any) -> Any:
    return func(*args)


def asgi_to_wsgi(asgi: Callable) -> Callable:
    """Run an ASGI app as a WSGI app, each request on its own event loop in the calling thread.

    No background loop thread (as a2wsgi keeps): uWSGI on PythonAnywhere imports the app and then
    forks its workers, and a thread started before the fork does not exist in them, so every request
    waited forever. Blocking work that FastAPI would hand to a thread pool runs inline instead; a WSGI
    worker serves one request per thread anyway, and nothing here depends on threads being allowed.
    """

    def wsgi(environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        anyio.to_thread.run_sync = _inline  # type: ignore[assignment]
        root_path = environ.get("SCRIPT_NAME", "").encode("latin-1").decode("utf-8")
        path = environ.get("PATH_INFO", "").encode("latin-1").decode("utf-8")
        headers = [
            (
                (key[5:] if key.startswith("HTTP_") else key).lower().replace("_", "-").encode("latin-1"),
                str(value).encode("latin-1"),
            )
            for key, value in environ.items()
            if (key.startswith("HTTP_") and key not in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"))
            or (key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value)
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split("/")[-1],
            "method": environ["REQUEST_METHOD"],
            "scheme": environ.get("wsgi.url_scheme", "http"),
            "path": root_path + path,
            "raw_path": (root_path + path).encode("utf-8"),
            "query_string": environ.get("QUERY_STRING", "").encode("latin-1"),
            "root_path": root_path,
            "headers": headers,
            "server": (environ.get("SERVER_NAME", "localhost"), int(environ.get("SERVER_PORT") or 80)),
            "client": (environ.get("REMOTE_ADDR", ""), int(environ.get("REMOTE_PORT") or 0)),
            "extensions": {},
        }
        stream = environ["wsgi.input"]
        try:
            left = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            left = 0
        answer: dict[str, Any] = {"body": []}

        async def run() -> None:
            nonlocal left
            done = asyncio.Event()

            async def receive() -> dict[str, Any]:
                nonlocal left
                if left > 0:
                    chunk = stream.read(min(CHUNK, left))
                    left = left - len(chunk) if chunk else 0
                    answer["received"] = left == 0
                    return {"type": "http.request", "body": chunk, "more_body": left > 0}
                if not answer.get("received"):
                    answer["received"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await done.wait()
                return {"type": "http.disconnect"}

            async def send(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    answer["status"] = message["status"]
                    answer["headers"] = [
                        (bytes(k).decode("latin-1"), bytes(v).decode("latin-1"))
                        for k, v in message.get("headers", [])
                    ]
                elif message["type"] == "http.response.body":
                    answer["body"].append(bytes(message.get("body", b"")))
                    if not message.get("more_body"):
                        done.set()

            await asgi(scope, receive, send)

        try:
            asyncio.run(run())
        except Exception:
            print(traceback.format_exc(), file=environ.get("wsgi.errors", sys.stderr))
            if "status" not in answer:
                answer.update(
                    status=500, headers=[("Content-Type", "text/plain")], body=[b"Internal Server Error"]
                )
        status = answer.get("status", 500)
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = ""
        start_response(f"{status} {phrase}".strip(), answer.get("headers", []))
        return [b"".join(answer["body"])]

    return wsgi


application = asgi_to_wsgi(app)


def guarded(is_signed_in: Callable[[Environ], bool], sign_in_url: str = "/login") -> Callable:
    """Triton behind the host site's sign-in: ``is_signed_in(environ)`` decides; anyone else is sent
    to ``sign_in_url`` (pages) or gets 401 (API calls)."""

    def wsgi(environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        if is_signed_in(environ):
            return application(environ, start_response)
        if environ.get("PATH_INFO", "").startswith("/api/"):
            start_response("401 Unauthorized", [("Content-Type", "application/json")])
            return [b'{"detail": "Sign in to use Triton."}']
        start_response("302 Found", [("Location", sign_in_url)])
        return [b""]

    return wsgi


def mount(host: Callable, prefix: str, triton: Callable = application) -> Callable:
    """The host site's WSGI app with Triton under ``prefix`` (e.g. "/triton"); every other path goes to
    the host as before."""
    prefix = "/" + prefix.strip("/")

    def wsgi(environ: Environ, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if path == prefix:
            start_response(
                "301 Moved Permanently", [("Location", environ.get("SCRIPT_NAME", "") + prefix + "/")]
            )
            return [b""]
        if path.startswith(prefix + "/"):
            environ = {
                **environ,
                "SCRIPT_NAME": environ.get("SCRIPT_NAME", "") + prefix,
                "PATH_INFO": path[len(prefix) :],
            }
            return triton(environ, start_response)
        return host(environ, start_response)

    return wsgi
