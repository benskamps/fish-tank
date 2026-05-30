import datetime as dt

from tank.clock import Clock, FakeClock


def test_real_clock_now_is_aware_utc():
    now = Clock().now()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now) == dt.timedelta(0)


def test_fake_clock_starts_at_given_time(fixed_now):
    c = FakeClock(fixed_now)
    assert c.now() == fixed_now


def test_fake_clock_advances(fixed_now):
    c = FakeClock(fixed_now)
    c.advance(dt.timedelta(minutes=5))
    assert c.now() == fixed_now + dt.timedelta(minutes=5)
