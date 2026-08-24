import asyncio

from src.resilience.guarded_call import GuardedCall, Level


def _run(coro):
    return asyncio.run(coro)


def test_primary_success_returns_value():
    async def primary():
        return "ok"

    async def fallback():
        return "fb"

    g = GuardedCall("t", primary, fallback, timeout=1, retries=0)
    assert _run(g()) == "ok"
    assert g.cb.level == Level.OK
    assert g.cb.fails == 0


def test_primary_fail_uses_fallback_and_degrades():
    async def primary():
        raise RuntimeError("boom")

    async def fallback():
        return "fb"

    g = GuardedCall("t", primary, fallback, timeout=1, retries=1, fail_threshold=3)
    assert _run(g()) == "fb"
    assert g.cb.fails == 1
    assert g.cb.level == Level.DEGRADED


def test_circuit_opens_after_threshold_then_short_circuits():
    primary_calls = {"n": 0}

    async def primary():
        primary_calls["n"] += 1
        raise RuntimeError("boom")

    async def fallback():
        return "fb"

    g = GuardedCall("t", primary, fallback, timeout=1, retries=0, fail_threshold=3, cool=60)
    _run(g())  # fails=1 -> DEGRADED
    _run(g())  # fails=2 -> DEGRADED
    assert g.cb.level == Level.DEGRADED
    _run(g())  # fails=3 -> DOWN
    assert g.cb.level == Level.DOWN
    before = primary_calls["n"]
    _run(g())  # 熔断冷却期内,直接 fallback,不再调 primary
    assert primary_calls["n"] == before


def test_timeout_counts_as_fail():
    async def primary():
        await asyncio.sleep(10)
        return "never"

    async def fallback():
        return "fb"

    g = GuardedCall("t", primary, fallback, timeout=0.05, retries=0, fail_threshold=1)
    assert _run(g()) == "fb"
    assert g.cb.level == Level.DOWN


def test_half_open_recovers_after_cooldown():
    clock = {"v": 0.0}

    def now():
        return clock["v"]

    async def sleep(s):
        return None

    seq = iter([False, False, True])

    async def primary():
        if not next(seq):
            raise RuntimeError("boom")
        return "ok"

    async def fallback():
        return "fb"

    g = GuardedCall(
        "t", primary, fallback,
        timeout=1, retries=0, fail_threshold=2, cool=60,
        now=now, sleep=sleep,
    )
    _run(g())  # fail -> fails=1 -> DEGRADED
    _run(g())  # fail -> fails=2 -> DOWN (opened_at = 0)
    assert g.cb.level == Level.DOWN
    clock["v"] = 10.0  # 冷却期内:直接 fallback
    assert _run(g()) == "fb"
    clock["v"] = 70.0  # 冷却到期:半开试探,primary 成功 -> OK
    assert _run(g()) == "ok"
    assert g.cb.level == Level.OK
