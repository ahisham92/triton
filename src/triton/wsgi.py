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

from collections.abc import Callable, Iterable
from typing import Any

from a2wsgi import ASGIMiddleware

from .api import app

application = ASGIMiddleware(app)

Environ = dict[str, Any]
StartResponse = Callable[..., Any]


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
