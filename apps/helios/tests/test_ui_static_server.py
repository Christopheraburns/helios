"""The UI server must not let a browser keep running a build that is gone."""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from apps.helios.ui import app as ui_app


@pytest.fixture
def server(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>current build</html>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(ui_app, "DIST", tmp_path)
    monkeypatch.setenv("HELIOS_API_URL", "https://api.example.test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui_app.HeliosUIHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url) as response:  # noqa: S310 - local test server
        return response.status, response.headers, response.read().decode()


def test_index_and_client_routes_are_revalidated(server):
    for path in ("/", "/index.html", "/crawler"):
        status, headers, body = get(server + path)
        assert status == 200 and "current build" in body
        assert headers["Cache-Control"] == "no-cache"


def test_hashed_assets_are_cached_and_config_is_not_stored(server):
    assert get(server + "/assets/index-abc123.js")[1]["Cache-Control"].endswith("immutable")
    assert get(server + "/config.js")[1]["Cache-Control"] == "no-store"


def test_an_asset_from_an_earlier_build_is_not_found(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(server + "/assets/CrawlerPage-oldhash.js")
    assert error.value.code == 404
