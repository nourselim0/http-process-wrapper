import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import ProcessWrapper

client = TestClient(app)


@pytest.fixture
def proc_stub():
    class ProcStub:
        def __init__(self, pid, returncode):
            self.pid = pid
            self.returncode = returncode

    return ProcStub


def test_list_procs_empty(monkeypatch):
    monkeypatch.setattr("app.main.processes_registry", {})
    resp = client.get("/procs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_procs_with_entries(monkeypatch, proc_stub):
    monkeypatch.setattr(
        "app.main.processes_registry",
        {
            "proc_a": ProcessWrapper.model_construct(
                name="proc_a",
                command=["echo", "test"],
                _proc=proc_stub(pid=None, returncode=None),
            ),
            "proc_b": ProcessWrapper.model_construct(
                name="proc_b",
                command=["echo", "test"],
                _proc=proc_stub(pid=42, returncode=0),
            ),
        },
    )

    resp = client.get("/procs")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2

    assert {
        "name": "proc_a",
        "command": ["echo", "test"],
        "pid": None,
        "returncode": None,
    } in data
    assert {
        "name": "proc_b",
        "command": ["echo", "test"],
        "pid": 42,
        "returncode": 0,
    } in data
