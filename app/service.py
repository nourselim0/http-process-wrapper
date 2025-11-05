from __future__ import annotations

from enum import Enum
import os
from asyncio import Task, create_task
from collections import deque
from datetime import datetime, timezone
from typing import Annotated as Ann
from typing import Any

from anyio import Lock, open_process
from anyio.abc import Process
from pydantic import BaseModel, StringConstraints, computed_field

processes_registry: dict[str, ProcessWrapper] = {}


class LogKind(Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


class LogLine(BaseModel):
    kind: LogKind
    timestamp: datetime
    text: str


class ProcessWrapper(BaseModel):
    name: Ann[str, StringConstraints(pattern=r"^[\w\-_]+$")]
    command: list[str]

    _proc: Process | None = None
    _lock: Lock
    _tasks: list[Task]
    _log_buffer: deque[LogLine]

    @computed_field
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @computed_field
    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    def model_post_init(self, context: Any, /) -> None:
        self._lock = Lock()
        self._tasks = []
        self._log_buffer = deque(maxlen=1000)

    async def start(self):
        self._proc = await open_process(self.command)

        self._tasks.append(create_task(self._read_stdout()))
        self._tasks.append(create_task(self._read_stderr()))

    async def _read_stdout(self):
        assert self._proc is not None and self._proc.stdout is not None
        async for line in self._proc.stdout:
            async with self._lock:
                self._append_log_line(kind=LogKind.STDOUT, text=line.decode())

    async def _read_stderr(self):
        assert self._proc is not None and self._proc.stderr is not None
        async for line in self._proc.stderr:
            async with self._lock:
                self._append_log_line(kind=LogKind.STDERR, text=line.decode())

    def _append_log_line(self, kind: LogKind, text: str):
        parts = text.splitlines(keepends=True)
        last_line = self._log_buffer[-1] if self._log_buffer else None
        start_idx = 0
        if last_line and last_line.kind == kind and not last_line.text.endswith("\n"):
            last_line.timestamp = datetime.now(timezone.utc)
            last_line.text += parts[0]
            start_idx = 1
        for i in range(start_idx, len(parts)):
            self._log_buffer.append(
                LogLine(kind=kind, timestamp=datetime.now(timezone.utc), text=parts[i])
            )

    async def write_stdin(self, line: str):
        assert self._proc is not None and self._proc.stdin is not None
        await self._proc.stdin.send((line + os.linesep).encode())

    async def tail(self, n: int, include_stderr: bool = True) -> list[LogLine]:
        async with self._lock:
            n = min(n, len(self._log_buffer))
            output: list[LogLine] = [None] * n  # type: ignore
            for line in reversed(self._log_buffer):
                if not include_stderr and line.kind == LogKind.STDERR:
                    continue
                output[n - 1] = line
                n -= 1
                if n == 0:
                    break
            return output

    async def stop(self, kill=False):
        if self._proc and self._proc.returncode is None:
            if kill:
                self._proc.kill()
            else:
                self._proc.terminate()
            await self._proc.wait()
        for task in self._tasks:
            task.cancel()
        self._tasks = []

    async def restart(self, kill_existing=False, clear_logs=False):
        await self.stop(kill=kill_existing)
        if clear_logs:
            async with self._lock:
                self._log_buffer.clear()
        await self.start()
