"""云 VLM:截图 → 战局语义描述(敌方波次/威胁方向)。

openai 兼容接口,支持通义千问-VL / GPT-4o / Gemini(openai 兼容端点)。
无 ``VLM_API_KEY`` 时 primary 抛错触发降级 fallback(空描述,退纯结构化特征)。
真实接入:在 ``.env`` 填 ``VLM_API_KEY`` + ``VLM_BASE_URL`` + ``VLM_MODEL`` 即生效。
"""

from __future__ import annotations

import base64
import os

from src.resilience.guarded_call import GuardedCall


def make_vlm(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> GuardedCall:
    key = api_key or os.getenv("VLM_API_KEY")
    base = base_url or os.getenv(
        "VLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    mdl = model or os.getenv("VLM_MODEL", "qwen-vl-max")
    has_key = bool(key)
    client = None
    if has_key:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, base_url=base)

    async def primary(screenshot_bytes: bytes) -> str:
        if not has_key or client is None:
            raise RuntimeError("VLM_API_KEY 未配置")
        b64 = base64.b64encode(screenshot_bytes).decode()
        resp = await client.chat.completions.create(
            model=mdl,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "看这张明日方舟战斗截图(分辨率1920x1080),按行报告:\n1.cost:数字(屏幕左下部署费用)\n2.待部署区干员头像位置:名字+屏幕坐标(如 桃金娘(120,950))\n3.建议Deploy的格子屏幕坐标(x,y)\n4.已下场干员+敌方数量位置\n简洁。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        return resp.choices[0].message.content or ""

    async def fallback(screenshot_bytes: bytes) -> str:
        # VLM 不可用:退纯 MAA 结构化 + MaaAI 特征,不走语义
        return ""

    return GuardedCall("vlm", primary, fallback, timeout=12.0, retries=1, fail_threshold=2, cool=90.0)
