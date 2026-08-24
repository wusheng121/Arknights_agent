import asyncio

from src.brain.llm_client import _coerce, make_brain
from src.game.copilot_schema import Action
from src.game.perception import GameState
from src.resilience.guarded_call import Level


def _run(coro):
    return asyncio.run(coro)


def test_coerce_parses_action():
    a = _coerce({"type": "Deploy", "name": "史尔特尔", "location": [4, 5], "direction": "Left"})
    assert a.type == "Deploy"
    assert a.name == "史尔特尔"
    assert a.location == (4, 5)
    assert a.direction == "Left"


def test_coerce_drops_unknown_fields():
    a = _coerce({"type": "SpeedUp", "bogus": 1})
    assert a.type == "SpeedUp"


def test_brain_fallback_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    brain = make_brain(api_key=None)
    state = GameState(stage="1-7", cost=20, step=1)
    out = _run(brain(state))  # 无 operators
    assert out.type == "SpeedUp"  # fallback 无 operators → SpeedUp
    assert brain.cb.level in (Level.DEGRADED, Level.DOWN)
