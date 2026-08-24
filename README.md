# arknights AI streamer

AI 虚拟主播 + 明日方舟 Agent。详见 `ai_streamer_plan.md`(原方案)与 `REVIEW.md`(评审 + P0 调研结论 + 云 API 修订)。

## 目录结构

```
src/
  resilience/guarded_call.py   云调用降级/熔断(超时+重试+fallback+熔断)
  game/copilot_schema.py      copilot-schema 数据结构(Action→to_maa)
  game/maapy_client.py        MAA 封装:SingleStep+回调闭环(MockMaapyClient + 真实 MaapyClient + 工厂)
  game/perception.py          感知融合:MAA结构化 + MaaAI + 云VLM → GameState
  game/vlm_client.py          云 VLM(openai 兼容,通义/GPT-4o/Gemini)
  game/singlestep_demo.py     mock 跑通 SingleStep 闭环
  brain/llm_client.py         DeepSeek 大脑(GameState → Action,thinking 模式)
  core/scheduler.py           聊天/玩法调度(决策>点名>普通>旁白)
  core/orchestrator.py        编排主循环:感知→调度→决策→执行
tests/                        单测
```

## 安装

```powershell
python -m pip install -r requirements.txt
python -m pip install openai        # DeepSeek/VLM(openai 兼容)
python -m pip install pytest
```

## 跑测试(mock,无需任何 key/环境)

```powershell
python -m pytest -q
```

## 跑 SingleStep mock demo

```powershell
$env:PYTHONIOENCODING="utf-8"
python -m src.game.singlestep_demo
```

## 真实接入(Phase 1)

### 1. MAA(手脚)
- 从 https://maa.plus 下载 MAA,解压;记录根目录路径(含 `MaaCore.dll` 与 `resource/`)。
- 从 MAA 仓库 `src/Python/asst/` 拷贝 `asst` 包到工作目录(或 `PYTHONPATH`),使其可 `from asst.asst import Asst`。
- 配置 `.env`(拷贝 `.env.example`):填 `MAA_RESOURCE_PATH`、`MAA_ADB_PATH`、`MAA_ADDRESS`。
- 装模拟器(MuMu/BlueStacks)+ 明日方舟国服,`adb connect` 到 `MAA_ADDRESS`。

### 2. DeepSeek(大脑)
- 申请 key: https://platform.deepseek.com/api_keys
- 填 `.env` 的 `DEEPSEEK_API_KEY`。
- 模型默认 `deepseek-v4-pro`,开 thinking 模式做游戏决策推理。

### 3. 云 VLM(视觉理解)
- 任选一家,填 `VLM_API_KEY` / `VLM_BASE_URL` / `VLM_MODEL`(见 `.env.example` 注释)。

### 4. 切到真实
- 真实分支需在真实环境验证 SingleStep 的执行/等待模型(MAA `append_task("SingleStep", ...)` 后的回调时序)。
- `orchestrator.game_loop` 当前默认 `create_client(mock=True)`;真实接入时传入真实 client:
  ```python
  from src.game.maapy_client import MaapyClient
  client = MaapyClient(resource_path="C:/MAA")
  await client.connect("adb.exe", "127.0.0.1:5555")
  await game_loop(client, steps=N)
  ```
- 填 key 后,`make_brain()` / `make_vlm()` 自动走真实 API;无 key 时自动降级 fallback(规则脚本 / 空描述),不报错。

## 合规(必读)

- 《人工智能生成合成内容标识办法》2025-09-01 已施行:直播画面须叠加显著「AI 生成」标识(虚拟场景起始 + 持续过程),并在流元数据写隐式标识/水印。
- 《人工智能拟人化互动服务管理暂行办法》2026-07-15 已施行:避开「虚拟伴侣」叙事、禁向未成年人提供。
- 生成式 AI 服务须备案并在直播间公示模型名 + 备案号(DeepSeek 备案号需查其公开信息)。
- 实时生成内容加:输入弹幕过滤 + 输出敏感词 + 兜底话术 + 延时直播 + 日志留存 ≥ 6 个月。
- 详见 `REVIEW.md` 第七节-3。

## 下一步

- 真实环境跑通 SingleStep 一关(验证回调时序)。
- Phase 1 Streamer MVP:B站弹幕 → DeepSeek → edge-tts → VTube Studio → OBS。
- 编排调度细化(聊天/玩法抢占实测)。
