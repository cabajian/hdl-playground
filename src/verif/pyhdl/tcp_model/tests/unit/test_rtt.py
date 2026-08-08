from tcp_model import ManualScheduler, TimerConfig
from tcp_model.core.rtt import RttEstimator


def test_first_sample_initializes():
    cfg = TimerConfig(rto_min=0.0, rto_max=100.0, rtt_g=0.0)
    est = RttEstimator(cfg)
    est.sample(0.5)
    assert est.srtt == 0.5
    assert est.rttvar == 0.25
    # rto = srtt + 4*rttvar = 0.5 + 1.0 = 1.5
    assert abs(est.rto - 1.5) < 1e-9


def test_subsequent_samples_smooth():
    cfg = TimerConfig(rto_min=0.0, rto_max=100.0, rtt_g=0.0)
    est = RttEstimator(cfg)
    est.sample(0.5)
    est.sample(0.5)  # identical sample -> rttvar shrinks, rto shrinks toward srtt
    assert est.srtt == 0.5
    assert est.rttvar < 0.25
    assert est.rto < 1.5


def test_rto_clamped_to_min():
    cfg = TimerConfig(rto_min=1.0, rto_max=60.0, rtt_g=0.0)
    est = RttEstimator(cfg)
    est.sample(0.01)
    assert est.rto == 1.0  # would be tiny, clamped up to rto_min


def test_backoff_doubles_and_caps():
    cfg = TimerConfig(rto_initial=1.0, rto_max=8.0)
    est = RttEstimator(cfg)
    est.backoff(); assert est.rto == 2.0
    est.backoff(); assert est.rto == 4.0
    est.backoff(); assert est.rto == 8.0
    est.backoff(); assert est.rto == 8.0  # capped


def test_scheduler_fires_in_time_order():
    s = ManualScheduler()
    log = []
    s.call_later(2.0, lambda: log.append("b"))
    s.call_later(1.0, lambda: log.append("a"))
    s.call_later(3.0, lambda: log.append("c"))
    s.advance(2.5)
    assert log == ["a", "b"]
    s.advance(1.0)
    assert log == ["a", "b", "c"]


def test_scheduler_cancel():
    s = ManualScheduler()
    log = []
    h = s.call_later(1.0, lambda: log.append("x"))
    s.cancel(h)
    fired = s.advance(2.0)
    assert log == []
    assert fired == 0


def test_scheduler_now_advances():
    s = ManualScheduler()
    assert s.now() == 0.0
    s.advance(1.5)
    assert s.now() == 1.5


def test_run_until_idle():
    s = ManualScheduler()
    log = []
    s.call_later(1.0, lambda: log.append(1))
    s.call_later(5.0, lambda: log.append(5))
    n = s.run_until_idle()
    assert n == 2
    assert log == [1, 5]
    assert s.pending == 0


def test_callback_can_reschedule():
    s = ManualScheduler()
    count = {"n": 0}

    def tick():
        count["n"] += 1
        if count["n"] < 3:
            s.call_later(1.0, tick)

    s.call_later(1.0, tick)
    s.advance(10.0)
    assert count["n"] == 3
