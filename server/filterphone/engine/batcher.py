"""איסוף בקשות לאצוות (micro-batching).

כשדף נטען, עשרות תמונות מגיעות בבת אחת. הרצה אחת על 8 תמונות זולה יותר
משמונה הרצות נפרדות. האוסף מחכה לכל היותר כמה מילישניות לבקשות נוספות.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Executor

import numpy as np


class Batcher:
    def __init__(self, fn: Callable[[np.ndarray], np.ndarray], executor: Executor,
                 max_batch: int, max_wait_ms: float):
        self._fn = fn
        self._executor = executor
        self._max = max_batch
        self._wait = max_wait_ms / 1000.0
        self._queue: asyncio.Queue[tuple[np.ndarray, asyncio.Future]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="batcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def submit(self, x: np.ndarray) -> np.ndarray:
        """x: טנזור יחיד (C, H, W). מחזיר את שורת הפלט שלו."""
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((x, fut))
        return await fut

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            items = [await self._queue.get()]
            deadline = loop.time() + self._wait
            while len(items) < self._max:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    items.append(await asyncio.wait_for(self._queue.get(), remaining))
                except TimeoutError:
                    break
            batch = np.stack([x for x, _ in items])
            try:
                out = await loop.run_in_executor(self._executor, self._fn, batch)
            except Exception as e:
                for _, fut in items:
                    if not fut.done():
                        fut.set_exception(e)
                continue
            for (_, fut), row in zip(items, out, strict=True):
                if not fut.done():
                    fut.set_result(row)
