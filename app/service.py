from __future__ import annotations

import os
from asyncio import Task, create_task
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated as Ann
from typing import Any

from anyio import Lock, create_memory_object_stream, open_process
from anyio.abc import Process
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
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
    _subscriber_streams: dict[
        MemoryObjectReceiveStream[LogLine], MemoryObjectSendStream[LogLine]
    ]

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
        self._subscriber_streams = {}

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
        for part in text.splitlines(keepends=True):
            line = LogLine(kind=kind, timestamp=datetime.now(timezone.utc), text=part)
            self._log_buffer.append(line)
            for receive_stream, send_stream in self._subscriber_streams.items():
                try:
                    send_stream.send_nowait(line)
                except Exception:  # pylint: disable=broad-except
                    print("Removing subscriber stream due to error")
                    create_task(self.unsubscribe_tail_stream(receive_stream))

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

    async def tail_stream(self, n: int) -> MemoryObjectReceiveStream[LogLine]:
        send_stream, receive_stream = create_memory_object_stream[LogLine](
            max_buffer_size=1000
        )
        queued_lines = await self.tail(n)
        self._subscriber_streams[receive_stream] = send_stream
        for line in queued_lines:
            await send_stream.send(line)
        return receive_stream

    async def unsubscribe_tail_stream(self, stream: MemoryObjectReceiveStream[LogLine]):
        await stream.aclose()
        send_stream = self._subscriber_streams.get(stream)
        if send_stream:
            await send_stream.aclose()
            self._subscriber_streams.pop(stream)

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
