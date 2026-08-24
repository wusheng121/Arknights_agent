import asyncio

from src.game.vlm_client import make_vlm


def _run(coro):
    return asyncio.run(coro)


def test_vlm_fallback_without_key(monkeypatch):
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.delenv("VLM_BASE_URL", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    vlm = make_vlm(api_key=None)
    out = _run(vlm(b"\x00\x01\x02"))
    assert out == ""  # fallback 退空描述(纯结构化)
