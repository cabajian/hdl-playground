"""RFC 6298 round-trip-time / retransmission-timeout estimator."""

from __future__ import annotations

from .timers import TimerConfig

_ALPHA = 1.0 / 8.0
_BETA = 1.0 / 4.0
_K = 4.0


class RttEstimator:
    def __init__(self, cfg: TimerConfig):
        self.cfg = cfg
        self.srtt: float | None = None
        self.rttvar: float | None = None
        self.rto: float = cfg.rto_initial

    def _clamp(self, v: float) -> float:
        return min(max(v, self.cfg.rto_min), self.cfg.rto_max)

    def sample(self, r: float) -> None:
        """Incorporate an RTT measurement ``r`` (seconds). RFC 6298 (2.2)/(2.3)."""
        if self.srtt is None:
            self.srtt = r
            self.rttvar = r / 2.0
        else:
            self.rttvar = (1 - _BETA) * self.rttvar + _BETA * abs(self.srtt - r)
            self.srtt = (1 - _ALPHA) * self.srtt + _ALPHA * r
        self.rto = self._clamp(self.srtt + max(self.cfg.rtt_g, _K * self.rttvar))

    def backoff(self) -> None:
        """RFC 6298 (5.5): double RTO on timeout, capped at rto_max."""
        self.rto = min(self.rto * 2.0, self.cfg.rto_max)
