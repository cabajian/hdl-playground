"""Configurable timer periods.

All periods are configurable (decision #8). Defaults are RFC-ish; under the
ManualScheduler used by unit tests, the wall-clock values are irrelevant because
time is advanced explicitly, so tests stay fast regardless.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimerConfig:
    # Retransmission (RFC 6298)
    rto_initial: float = 1.0
    rto_min: float = 1.0
    rto_max: float = 60.0
    rtt_g: float = 0.001        # clock granularity G
    max_retransmits: int = 12   # give up / abort after this many RTOs

    # Persist (zero-window probing)
    persist_min: float = 1.0
    persist_max: float = 60.0

    # Keepalive (RFC 9293 3.8.4: MUST default to off, MUST be configurable)
    keepalive_idle: float | None = None   # None => keepalive disabled
    keepalive_interval: float = 75.0      # gap between unanswered probes
    keepalive_count: int = 9              # unanswered probes before abort

    # TIME-WAIT uses 2 * msl
    msl: float = 1.0

    # Delayed ACK: None => ACK immediately (simplest / most deterministic).
    # Set to a delay to coalesce ACKs (ack every 2nd full segment or on timeout).
    delayed_ack: float | None = None

    # User timeout (RFC 9293 3.8.3): abort if data remains unacknowledged this
    # long. None => disabled.
    user_timeout: float | None = None

    # --- engine behavior knobs (not timers; housed here so a single config
    #     object travels through helpers and adapters) ---
    # Nagle / sender SWS avoidance (RFC 9293 3.7.4). Default OFF for a
    # verification stimulus model: holding the sub-MSS tail of a message until
    # an ACK arrives would surprise testbenches pumping fixed-size messages.
    # Turn on for RFC-faithful pacing.
    nagle: bool = False
    # Receiver SWS avoidance (RFC 9293 3.8.6.2.2): do not advance the
    # advertised window right edge in increments smaller than
    # min(MSS, buffer/2). Default ON per the RFC.
    sws_avoidance: bool = True
