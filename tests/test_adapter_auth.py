import io
import json

from emhass_perfect_world.lab import adapter


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(options, seen):
    def fake(req, timeout=0):
        seen.append((req.full_url, dict(req.header_items())))
        if req.full_url.startswith(adapter.SUPERVISOR_API):
            return _Resp(json.dumps({"data": {"options": options}}).encode())
        return _Resp(b'{"ok": 1}')
    return fake


def test_header_from_production_options(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup")
    seen = []
    monkeypatch.setattr(adapter.urllib.request, "urlopen", _fake_urlopen({"emhass_api_token": "0123456789abcdef"}, seen))
    assert adapter.prod_auth_headers() == {"Authorization": "Bearer 0123456789abcdef"}
    assert seen[0][0].endswith("/addons/5b918bf2_emhass/info")


def test_no_header_without_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup")
    monkeypatch.setattr(adapter.urllib.request, "urlopen", _fake_urlopen({}, []))
    assert adapter.prod_auth_headers() == {}


def test_prod_get_sends_bearer(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup")
    seen = []
    monkeypatch.setattr(adapter.urllib.request, "urlopen", _fake_urlopen({"emhass_api_token": "0123456789abcdef"}, seen))
    assert adapter.fetch_prod_config() == {"ok": 1}
    url, headers = seen[-1]
    assert url == adapter.PROD_API + "/get-config"
    assert headers["Authorization"] == "Bearer 0123456789abcdef"
