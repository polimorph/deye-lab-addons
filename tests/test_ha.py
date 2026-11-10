import io
import urllib.error

import pytest

from emhass_perfect_world.lab import ha as ha_mod


def _client(monkeypatch, behaviour):
    c = ha_mod.HA(base="http://x", token="t")
    monkeypatch.setattr(c, "_get", behaviour)
    return c


def test_exists_true(monkeypatch):
    assert _client(monkeypatch, lambda path, params=None, timeout=30: {"state": "1"}).exists("sensor.a") is True


def test_exists_false_on_404(monkeypatch):
    def boom(path, params=None, timeout=30):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))
    assert _client(monkeypatch, boom).exists("sensor.a") is False


def test_exists_propagates_502(monkeypatch):
    def boom(path, params=None, timeout=30):
        raise urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b""))
    with pytest.raises(urllib.error.HTTPError):
        _client(monkeypatch, boom).exists("sensor.a")
