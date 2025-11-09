from collections import deque
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import LogKind, LogLine, ProcessWrapper

client = TestClient(app)


@pytest.fixture
def proc_stub():
    class ProcStub:
        def __init__(self, pid, returncode):
            self.pid = pid
            self.returncode = returncode

    return ProcStub


@pytest.fixture
def log_lines():
    return deque(
        (
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                text="Start\n",
            ),
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc),
                text="Output line 1\n",
            ),
            LogLine(
                kind=LogKind.STDERR,
                timestamp=datetime(2024, 1, 1, 12, 0, 2, tzinfo=timezone.utc),
                text="Error line 1\n",
            ),
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=datetime(2024, 1, 1, 12, 0, 3, tzinfo=timezone.utc),
                text="Output line 2\n",
            ),
            LogLine(
                kind=LogKind.STDERR,
                timestamp=datetime(2024, 1, 1, 12, 0, 4, tzinfo=timezone.utc),
                text="Error line 2\n",
            ),
        )
    )


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


def test_tail_proc_output(monkeypatch, proc_stub, log_lines):
    monkeypatch.setattr(
        "app.main.processes_registry",
        {
            "test_proc": ProcessWrapper.model_construct(
                name="test_proc",
                _proc=proc_stub(pid=42, returncode=None),
                _log_buffer=log_lines,
            ),
        },
    )

    resp = client.get("/procs/test_proc/tail?n=2&include_stderr=true")
    assert resp.status_code == 200

    data = resp.json()
    assert data == [
        {
            "kind": "stdout",
            "timestamp": "2024-01-01T12:00:03Z",
            "text": "Output line 2\n",
        },
        {
            "kind": "stderr",
            "timestamp": "2024-01-01T12:00:04Z",
            "text": "Error line 2\n",
        },
    ]


def test_tail_proc_output_without_stderr(monkeypatch, proc_stub, log_lines):
    monkeypatch.setattr(
        "app.main.processes_registry",
        {
            "test_proc": ProcessWrapper.model_construct(
                name="test_proc",
                _proc=proc_stub(pid=42, returncode=0),
                _log_buffer=log_lines,
            ),
        },
    )

    resp = client.get("/procs/test_proc/tail?n=2&include_stderr=false")
    assert resp.status_code == 200

    data = resp.json()
    assert data == [
        {
            "kind": "stdout",
            "timestamp": "2024-01-01T12:00:01Z",
            "text": "Output line 1\n",
        },
        {
            "kind": "stdout",
            "timestamp": "2024-01-01T12:00:03Z",
            "text": "Output line 2\n",
        },
    ]


def test_tail_proc_output_text(monkeypatch, proc_stub, log_lines):
    monkeypatch.setattr(
        "app.main.processes_registry",
        {
            "test_proc": ProcessWrapper.model_construct(
                name="test_proc",
                _proc=proc_stub(pid=42, returncode=0),
                _log_buffer=log_lines,
            ),
        },
    )

    resp = client.get("/procs/test_proc/tail-text?n=2")
    assert resp.status_code == 200

    data = resp.json()
    assert data == [
        "2024-01-01T12:00:03+00:00 | Output line 2\n",
        "2024-01-01T12:00:04+00:00 | Error line 2\n",
    ]


def test_write_process_input(monkeypatch, proc_stub):
    # pylint: disable=protected-access
    test_proc = ProcessWrapper.model_construct(
        name="test_proc",
        command=["bash"],
        _proc=proc_stub(pid=42, returncode=None),
        _log_buffer=deque(
            (
                LogLine(
                    kind=LogKind.STDOUT,
                    timestamp=datetime.now(timezone.utc),
                    text="Full Line\n",
                ),
                LogLine(
                    kind=LogKind.STDOUT,
                    timestamp=datetime.now(timezone.utc),
                    text="Partial Line: ",
                ),
            )
        ),
    )
    monkeypatch.setattr("app.main.processes_registry", {"test_proc": test_proc})

    async def mock_write_stdin(self, line: str):
        self._append_log_line(LogKind.STDOUT, line + "\n")

    monkeypatch.setattr(
        "app.main.ProcessWrapper.write_stdin",
        mock_write_stdin,
    )

    resp = client.post(
        "/procs/test_proc/write",
        content="Continuation\nAnother Line",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 202

    assert test_proc._log_buffer == deque(
        (
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=test_proc._log_buffer[0].timestamp,
                text="Full Line\n",
            ),
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=test_proc._log_buffer[1].timestamp,
                text="Partial Line: Continuation\n",
            ),
            LogLine(
                kind=LogKind.STDOUT,
                timestamp=test_proc._log_buffer[2].timestamp,
                text="Another Line\n",
            ),
        )
    )
