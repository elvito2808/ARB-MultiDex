"""Serialize capture, validation, publication and disconnect for one market."""

import time
from threading import RLock
from uuid import uuid4


class MarketError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def invalid(reason):
    return MarketError("INVALID_QUOTE", reason)


def unavailable(reason):
    return MarketError("SOURCE_UNAVAILABLE", reason)


def utc_ms():
    return time.time_ns() // 1_000_000


class Feed:
    def __init__(
        self,
        source_id,
        base_id,
        settlement_id,
        base_scale,
        settlement_scale,
        *,
        clock=utc_ms,
        epoch_id=None,
    ):
        self.source_id = source_id
        self.base_id = base_id
        self.settlement_id = settlement_id
        self.base_scale = base_scale
        self.settlement_scale = settlement_scale
        self.clock = clock
        self._epoch_id = epoch_id or (lambda: uuid4().hex)
        self.lock = RLock()  # Serialize market reads across relationship bindings.
        self.state_lock = RLock()  # Disconnect can cancel a slow read or fee lookup.
        self.epoch = None
        self.route = None
        self.recovering = False
        self.recovered_at = None
        self.last = None
        self.last_epoch = None

    def now(self):
        try:
            value = self.clock()
        except OSError as exc:
            raise unavailable("UTC clock unavailable") from exc
        if type(value) is not int:
            raise unavailable("UTC clock unavailable")
        return value

    def connected(self, route=None):
        with self.state_lock:
            if self.epoch is not None and self.route != route:
                self.epoch = None
                self.recovering = True
            if self.epoch is None:
                self.recovered_at = self.now() if self.recovering else None
                self.epoch = self._epoch_id()
                self.route = route

    def disconnect(self):
        with self.state_lock:
            self.epoch = None
            self.route = None
            self.recovering = True

    def validate_observation(self, snap, now):
        if self.epoch is None:
            raise unavailable("disconnected")
        if snap.observed_ms > now:
            raise invalid("future observation")
        if self.recovered_at is not None and snap.observed_ms < self.recovered_at:
            raise invalid("observation predates recovery")
        last = self.last
        if last is None:
            return
        if snap.observed_ms < last.observed_ms or snap.position < last.position:
            raise invalid("observation regressed")
        same_point = snap.observed_ms == last.observed_ms or snap.position == last.position
        if same_point and (snap != last or self.last_epoch != self.epoch):
            raise invalid("conflicting or replayed observation")

    def accept(self, snap):
        self.last, self.last_epoch = snap, self.epoch
        self.recovering = False
