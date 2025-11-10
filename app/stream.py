"""In-memory stream primitives.

StreamItem: lightweight container for a single stream record.
InMemoryStream: an append-only, fixed-retention, in-process stream with
per-consumer offsets (seq numbers) and async subscribe(replay+live) support.

This file is intentionally self-contained (no dependency on the project's
LogKind/LogLine types) so you can import it into `service.py` later without a
circular import. Consumers can use `kind` as any type (string, enum instance,
etc.).

Usage summary (short):
- stream = InMemoryStream(maxlen=1000)
- await stream.append(kind, text)        # producer appends and notifies
- async for item in stream.subscribe(seq):  # consumer obtains past+future items
- await stream.close()                   # close and notify subscribers

Behavior notes:
- Each appended item receives a monotonically increasing `seq` integer.
- `subscribe(start_seq)` replays any retained items starting from `start_seq`
  (clamped to the head if the requested offset was dropped due to retention),
  then yields live items as they are appended.
- Use `maxlen` to cap memory; older items are dropped (head sequence advances).
- This is an in-memory, single-process stream primitive; for multi-node or
  long-term retention use Kafka/Redis Streams or another durable system.

"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Deque, List, Optional

import anyio


@dataclass
class StreamItem:
    """A single item stored in the InMemoryStream.

    Fields:
    - seq: monotonically increasing sequence number for ordering and offsets
    - timestamp: UTC timestamp assigned when the item was appended
    - kind: user-defined kind (could be a string or an enum like LogKind)
    - text: the message/body/payload (string recommended for logs)
    """

    seq: int
    timestamp: datetime
    kind: Any
    text: str


class InMemoryStream:
    """Append-only in-memory stream with per-consumer offsets.

    Core ideas:
    - Keep a bounded deque of StreamItem objects.
    - Assign a sequence number to each appended item (self._next_seq).
    - Maintain head_seq (seq of first retained item) and next_seq.
    - Consumers call `subscribe(start_seq)` which yields retained items from
      `start_seq` and then waits for and yields future items.

    This provides Kafka-like offset/replay semantics in memory for a single
    process. It is suitable for short-term replay and realtime fanout within a
    single process.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._lock = anyio.Lock()
        self._cond = anyio.Condition()
        self._buffer: Deque[StreamItem] = deque(maxlen=maxlen)
        self._head_seq: int = 0
        self._next_seq: int = 0
        self._maxlen = maxlen
        self._closed: bool = False

    @property
    def head_seq(self) -> int:
        return self._head_seq

    @property
    def next_seq(self) -> int:
        return self._next_seq

    async def append(self, kind: Any, text: str) -> int:
        """Append a new item to the stream and notify subscribers.

        Returns the assigned sequence number.
        """
        async with self._lock:
            seq = self._next_seq
            item = StreamItem(seq=seq, timestamp=datetime.now(timezone.utc), kind=kind, text=text)
            self._buffer.append(item)
            self._next_seq += 1
            # recompute head_seq in case the deque dropped old entries
            self._head_seq = self._next_seq - len(self._buffer)

        # notify waiting subscribers outside the main lock
        await self._notify_all()
        return seq

    async def _notify_all(self) -> None:
        # notify_all must acquire the condition's lock
        async with self._cond:
            self._cond.notify_all()

    async def close(self) -> None:
        """Mark stream closed and wake subscribers (they will stop when no
        more items are available).
        """
        self._closed = True
        await self._notify_all()

    async def subscribe(self, start_seq: Optional[int] = None) -> AsyncIterator[StreamItem]:
        """Async generator yielding StreamItem objects from start_seq (inclusive).

        Args:
            start_seq: If None, subscription starts at future items only
                       (i.e. next_seq at time of subscribe). If provided, the
                       subscription will replay retained items starting from
                       start_seq (or head_seq if start_seq < head_seq) and
                       then yield live items.
        """
        # choose initial offset
        async with self._lock:
            if start_seq is None:
                offset = self._next_seq
            else:
                # clamp if requested start is older than retained head
                if start_seq < self._head_seq:
                    offset = self._head_seq
                else:
                    offset = start_seq

        try:
            while True:
                # yield any buffered items from offset onwards
                async with self._lock:
                    head_seq = self._head_seq
                    # make a snapshot of the buffer under the lock
                    buf: List[StreamItem] = list(self._buffer)
                    # ensure offset is not below head_seq
                    if offset < head_seq:
                        offset = head_seq
                    idx = offset - head_seq
                    if idx < len(buf):
                        for item in buf[idx:]:
                            yield item
                            offset = item.seq + 1
                        # after yielding buffered items, loop to wait for new ones
                        continue

                    # if closed and no new items will ever arrive, break
                    if self._closed:
                        break

                # wait for a notification that new items are available
                async with self._cond:
                    await self._cond.wait()
                    # loop will re-evaluate the buffer and yield new items

        except Exception:
            # allow normal cancellation and other exceptions to bubble up, but
            # ensure generator exits cleanly
            raise


# ---------------------------------------------------------------------------
# Discussion / design notes
# ---------------------------------------------------------------------------
#
# This in-memory stream provides Kafka-like semantics for short-term replay
# inside a single process: each item gets a monotonic `seq`, and subscribers
# track their own offset (seq) and can replay retained items. Example uses:
# - short-term replay when a webhook reconnects and requests missed entries
# - live consumer + replay from recent history via subscribe(start_seq)
#
# Tradeoffs and operational notes:
# - Retention: `maxlen` bounds memory. When the buffer evicts old items the
#   `head_seq` advances. Subscribers that request an offset older than
#   `head_seq` must accept being clamped forward or be told they missed data.
# - Concurrency: all operations that touch seq/head/buffer must be guarded by
#   `_lock` to avoid races; the `_cond` condition is used to notify waiting
#   subscribers of new items.
# - Backpressure: this design decouples producers from consumers; each
#   consumer pulls at its own pace. The tradeoff is memory (retained items)
#   and retention policy. For long-term or many slow consumers prefer durable
#   systems (Kafka/Redis Streams) instead of in-memory retention.
# - Multi-process scale: this implementation is single-process only. For
#   multiple nodes you need an external system.
# - Integration with existing code: if you already have a `_log_buffer` you
#   can adapt this pattern by assigning seqs when appending and using the
#   existing buffer as the authoritative history. Make sure notifications are
#   emitted (via `_cond`) after appending.
#
# Example consumer API:
#
# async for item in stream.subscribe(start_seq=some_seq):
#     process(item)
#
# Example producer usage:
#
# seq = await stream.append(kind, text)
#
# Close the stream to signal subscribers that no more items will arrive:
# await stream.close()
#
#
# When to prefer this over per-subscriber send/receive fan-out:
# - Use this `InMemoryStream` when you want replay-by-offset semantics and
#   each consumer to have its own pointer into the stream (Kafka-like).
# - Use per-subscriber memory-object-streams (fan-out) when you need strict
#   broadcast semantics and want to handle slow consumers with per-subscriber
#   buffers/timeouts (these two approaches can be combined: use the
#   InMemoryStream as the authoritative history and per-subscriber streams for
#   live delivery).
#
# If you'd like, I can integrate this class into `ProcessWrapper` and make
# `subscribe()` return the async iterator from `InMemoryStream.subscribe()`,
# and/or add an adapter that produces per-subscriber `anyio` streams for
# webhook-style clients.
#
