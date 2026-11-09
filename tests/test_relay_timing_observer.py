import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("measure_relay_timing", Path(__file__).parents[1] / "scripts/measure_relay_timing.py")
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


def status(stamp, **kwargs):
    return {"origins": [dict(module_id="lab", checked_at=stamp, status="connected",
                             server_pid=42, timings_ms={"total": 12}, **kwargs)]}


def test_initial_cache_and_repeated_status_are_not_new_cycles():
    o = timing.Observer()
    o.observe(status("old"), 0)
    o.observe(status("old"), 1)
    assert o.summary()["lab"]["observed_updates"] == 0
    o.observe(status("new"), 2)
    o.observe(status("newer"), 3)
    assert o.summary()["lab"]["observed_update_interval_s"]["mean"] == 1
    assert o.summary()["lab"]["timings_ms"]["total"]["count"] == 2
    assert o.summary()["lab"]["missed_cycles"] is None


def test_failed_updates_and_recovery_are_not_hidden():
    o = timing.Observer()
    o.observe(status("initial"), 0)
    bad = status("failed"); bad["origins"][0].update(status="unavailable", timings_ms={})
    o.observe(bad, 1)
    o.observe(status("recovery"), 2)
    s = o.summary()["lab"]
    assert s["non_connected_updates"] == 1
    assert s["timings_ms"]["total"]["count"] == 1


def test_statistics_and_no_sensitive_fields_retained():
    assert timing.distribution(range(1, 21))["p95_nearest_rank"] == 19
    o = timing.Observer()
    s = status("first", message="secret", samples=[{"value": "private"}])
    s["origins"][0]["timings_ms"].update(total=float("nan"), token=123)
    o.observe(s, 1)
    assert o.rows[0]["timings_ms"] == {}
    assert "message" not in o.rows[0] and "samples" not in o.rows[0]
