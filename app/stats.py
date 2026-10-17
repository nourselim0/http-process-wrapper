from asyncio import Task, create_task
from typing import ClassVar

import psutil
from anyio import sleep
from pydantic import BaseModel


class ProcessStats(BaseModel):
    cpu_percent: float | None = None
    mem_usage_mb: float | None = None
    mem_percent: float | None = None
    num_threads: int | None = None

    _proc: psutil.Process | None = None

    _task: ClassVar[Task | None] = None

    def start(self, pid: int):
        self._proc = psutil.Process(pid)
        self._proc.cpu_percent(interval=None)  # establish baseline
        self.start_loop()

    @classmethod
    def start_loop(cls):
        if not cls._task or cls._task.done():
            cls._task = create_task(cls.monitor_loop())

    @classmethod
    async def monitor_loop(cls):
        from .service import processes_registry

        while True:
            if not processes_registry:
                cls._task = None
                return

            for process in processes_registry.values():
                stats = process.stats
                if stats._proc is None or not stats._proc.is_running():
                    continue
                with stats._proc.oneshot():
                    stats.cpu_percent = stats._proc.cpu_percent(interval=None)
                    stats.mem_usage_mb = stats._proc.memory_info().rss / (1024 * 1024)
                    stats.mem_percent = stats._proc.memory_percent(memtype="rss")
                    stats.num_threads = stats._proc.num_threads()

            await sleep(0.2)
