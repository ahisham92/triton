"""Triton as a WSGI app mounted under /triton inside another site, behind that site's sign-in."""

import io
import json
import os
import signal
import wsgiref.util

import pytest

from triton.wsgi import application, guarded, mount


def call(app, path, method="GET", body=b"", content_type="", root="", query=""):
    environ = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": root,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    wsgiref.util.setup_testing_defaults(environ)
    out = {}

    def start_response(status, headers, exc_info=None):
        out["status"], out["headers"] = int(status.split()[0]), dict(headers)

    data = b"".join(app(environ, start_response))
    return out["status"], out["headers"], data


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))


def test_mounted_under_a_prefix():
    status, _, page = call(application, "/", root="/triton")
    assert status == 200 and b'src="static/app.js?v=' in page  # relative, so it works under /triton/
    status, _, _ = call(application, "/static/app.js", root="/triton")
    assert status == 200
    body = json.dumps({"info": {"name": "Online"}}).encode()
    status, _, data = call(application, "/api/projects", "POST", body, "application/json", root="/triton")
    assert status == 201 and json.loads(data)["info"]["name"] == "Online"
    status, _, data = call(application, "/api/projects", root="/triton")
    assert [p["name"] for p in json.loads(data)] == ["Online"]


def test_guard_uses_the_host_sign_in():
    app = guarded(lambda environ: "signed=1" in environ.get("HTTP_COOKIE", ""), sign_in_url="/login")
    status, headers, _ = call(app, "/", root="/triton")
    assert status == 302 and headers["Location"] == "/login"
    status, _, _ = call(app, "/api/projects", root="/triton")
    assert status == 401


def test_workbook_upload_through_wsgi():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Pile(1)-QP"
    ws.append(["Structural element", "Node", "Local number", "X", "Y", "Z", "N"])
    ws.append(["EmbeddedBeam_1", 1, 1, 0.0, 0.0, 0.0, -10.0])
    buf = io.BytesIO()
    wb.save(buf)
    boundary = "tritonboundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="w.xlsx"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + buf.getvalue()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, _, data = call(
        application,
        "/api/workbooks/check",
        "POST",
        body,
        f"multipart/form-data; boundary={boundary}",
        "/triton",
    )
    assert status == 200 and json.loads(data)["file"] == "w.xlsx"


def test_mount_keeps_the_host_site():
    def site(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"project control"]

    app = mount(site, "/triton", guarded(lambda environ: True))
    assert call(app, "/crs")[2] == b"project control"
    status, headers, _ = call(app, "/triton")
    assert status == 301 and headers["Location"] == "/triton/"
    status, _, page = call(app, "/triton/")
    assert status == 200 and b"<title>Triton</title>" in page
    assert call(app, "/triton/api/projects")[0] == 200


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_answers_in_a_forked_worker():
    """uWSGI (PythonAnywhere) imports the app and then forks its workers: a request in the fork must
    still be answered, not wait on a loop thread that stayed behind in the parent."""
    pid = os.fork()
    if pid == 0:  # the worker
        signal.alarm(20)
        status, _, _ = call(application, "/api/projects", root="/triton")
        os._exit(0 if status == 200 else 1)
    _, code = os.waitpid(pid, 0)
    assert os.WIFEXITED(code) and os.WEXITSTATUS(code) == 0


def test_an_error_is_a_500_and_logged():
    errors = io.StringIO()

    async def broken(scope, receive, send):
        raise RuntimeError("boom")

    from triton.wsgi import asgi_to_wsgi

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/", "wsgi.input": io.BytesIO(), "wsgi.errors": errors}
    wsgiref.util.setup_testing_defaults(environ)
    out = {}
    body = b"".join(asgi_to_wsgi(broken)(environ, lambda s, h, e=None: out.update(status=s)))
    assert out["status"] == "500 Internal Server Error" and body == b"Internal Server Error"
    assert "RuntimeError: boom" in errors.getvalue()


def test_a_piece_through_wsgi():
    """The raw body of a PUT reaches the app through the WSGI bridge intact."""
    status, _, data = call(application, "/api/uploads", "POST", b'{"filename": "w.xlsb"}', "application/json")
    upload_id = json.loads(data)["id"]
    piece = bytes(range(256)) * 1000  # several reads of the bridge's buffer
    status, _, data = call(
        application, f"/api/uploads/{upload_id}", "PUT", piece, "application/octet-stream", query="offset=0"
    )
    assert status == 200 and json.loads(data)["received"] == len(piece)
