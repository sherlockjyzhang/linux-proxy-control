import json

import pytest

from backend import probe_job


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(next(self.responses))


def test_reconcile_reapplies_persisted_mappings(monkeypatch):
    opener = FakeOpener(
        [
            {"mappings": [{"ip": "192.0.2.7/32", "selection": {"kind": "region", "value": "Korea"}, "allow_cross_region_fallback": False}]},
            {"mode": "real", "applied": True},
        ]
    )
    monkeypatch.setattr(probe_job.urllib.request, "build_opener", lambda *args: opener)

    result = probe_job.reconcile()

    assert result == {"mapping_count": 1, "applied": True}
    assert [request.get_method() for request, _ in opener.requests] == ["GET", "PUT"]
    assert json.loads(opener.requests[1][0].data) == {
        "mappings": [
            {
                "ip": "192.0.2.7/32",
                "selection": {"kind": "region", "value": "Korea"},
                "allow_cross_region_fallback": False,
            }
        ]
    }


def test_reconcile_does_not_write_when_no_mappings(monkeypatch):
    opener = FakeOpener([{"mappings": []}])
    monkeypatch.setattr(probe_job.urllib.request, "build_opener", lambda *args: opener)

    assert probe_job.reconcile() == {"mapping_count": 0, "applied": False}
    assert [request.get_method() for request, _ in opener.requests] == ["GET"]


def test_reconcile_rejects_unapplied_real_result(monkeypatch):
    opener = FakeOpener([{"mappings": [{"ip": "192.0.2.7/32"}]}, {"mode": "real", "applied": False}])
    monkeypatch.setattr(probe_job.urllib.request, "build_opener", lambda *args: opener)

    with pytest.raises(RuntimeError, match="were not applied"):
        probe_job.reconcile()
