# AI 明日方舟主播 - 待办与规划

## 当前状态 (2026-09-02)

### ✅ 已完成

- **L1 通关验证**: 1-7 Stars=3 (煌单核), AT-7 Stars=3 (专家作业)
- **L2 知识库**: ArknightsGameData + 波次解析 + 敌人属性 + 干员特性 + RAG
- **sim 模拟器**: 67% 通过率 (13个bug修复, 含fragment串行/AUTO技能/atk_scale)
- **条件化作业**: kills/costs 条件化, 不用固定时间
- **自反思记忆 (P3)**: 真机结果→memory.py→自动promote原则
- **AI 主播框架**: EventBus + Commentator(LLM解说) + TTS(edge-tts+MCI) + 弹幕(mock)
- **BattleMonitor 安全网**: 实时截图+感知+应急干预
- **UI 导航器**: 界面检测(ToggleSettingsMenu 0.967) + 游戏启动 + back key退出子界面
- **MAA 触控**: minitouch 模式 (与 MAA GUI 一致)
- **MAA 编队+开始战斗**: BattleStartPre → Formation → BattleStartAll 正常工作

### ❌ 卡住的问题

**MAA Python API 无法从主界面导航到关卡。**

- `Copilot` filename 模式: 禁用导航
- `Copilot` copilot_list 模式: v6.16.8 崩溃 (C++ 异常, JSON 格式不确定)
- `Custom` + `task_names=["1-7"]`: MAA 找不到 StageTheme 按钮
- MAA GUI 能正常导航, Python API 不能 (原因未确定)
- `StartUp` 直接任务类型: 已尝试, 仍然找不到 StageTheme

### 后续突破方向

1. **升级 MAA 到最新版**: copilot_list 可能已修复
2. **用 MaaFramework** (新框架): `pip install maafw`, Python 支持更好
3. **用 MAA CLI**: 命令行接口可能行为更接近 GUI
4. **逆向 MAA GUI**: Process Monitor 拦截 GUI 发给 DLL 的确切 JSON
5. **用 MAA GUI 自动化**: UI 自动化工具控制 MAA GUI

---

## 模块清单

| 模块 | 文件 | 状态 |
|------|------|------|
| LLM 管道 | `src/brain/pipeline.py` | ✅ |
| LLM 客户端 | `src/brain/llm_client.py` | ✅ |
| RAG 检索 | `src/data/rag_retriever.py` | ✅ |
| 专家作业爬取 | `src/data/expert_crawler.py` | ✅ |
| 离线蒸馏 | `src/data/pass1_annotate.py` + `pass2_aggregate.py` | ✅ |
| 统计模式 | `src/data/pattern_extractor.py` | ✅ |
| 干员特性 | `src/data/oper_profile.py` | ✅ |
| 地图信息 | `src/data/map_info.py` | ✅ |
| 波次解析 | `src/data/wave_parser.py` | ✅ |
| 敌人属性 | `src/data/enemy_lookup.py` | ✅ |
| 关卡工具 | `src/data/stage_util.py` | ✅ |
| 后处理 | `src/data/job_post_process.py` | ✅ |
| MAA 封装 | `src/game/maapy_client.py` | ✅ |
| TileCalc | `src/game/tile_calc.py` | ✅ |
| CV 感知 | `src/game/cv_perception.py` | ✅ |
| 技能检测 | `src/game/skill_detector.py` | ✅ |
| 安全网 | `src/game/battle_monitor.py` | ✅ |
| UI 导航 | `src/game/ui_navigator.py` | ⚠️ |
| sim 核心 | `src/sim/game_state.py` | ✅ |
| sim 数据 | `src/sim/data_loader.py` | ✅ |
| sim 范围 | `src/sim/range_calc.py` | ✅ |
| sim 记忆 | `src/sim/memory.py` | ✅ |
| sim 验证 | `src/sim/validate.py` | ✅ |
| sim 校准 | `src/sim/calibrator.py` | ✅ |
| 事件总线 | `src/streamer/event_bus.py` | ✅ |
| 解说生成 | `src/streamer/commentator.py` | ✅ |
| TTS 引擎 | `src/streamer/tts_engine.py` | ✅ |
| VTube | `src/streamer/vtube_controller.py` | stub |
| OBS | `src/streamer/obs_controller.py` | stub |
| 弹幕 | `src/streamer/danmaku_reader.py` | mock |
| 主控 | `src/streamer/streamer.py` | ✅ |
| 主入口 | `src/real_run.py` | ✅ |

---

## 环境配置

```
Python 3.14
DeepSeek API (DEEPSEEK_API_KEY in .env)
edge-tts (pip install edge-tts)
MAA v6.16.8 (C:\Users\slient\Downloads\MAA-v6.16.8-win-x64)
MAA 源码 (C:\demo\MaaAssistantArknights-dev-v2)
MuMu 模拟器 (127.0.0.1:16384, 1920×1080, Android 15)
ADB (C:\Program Files\Netease\MuMu\nx_main\adb.exe)
MAA 触控模式: minitouch
```

---

## 数据源

| 数据源 | 用途 | 位置 |
|--------|------|------|
| MAA 安装 | 执行/感知/模板 | `C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\` |
| MAA 源码 | 参考 | `C:\demo\MaaAssistantArknights-dev-v2\` |
| ArknightsGameData | 游戏数据 | `data/gamedata/` |
| prts.wiki | 补充数据 | `https://prts.wiki/api.php` |
| MuMu 模拟器 | 游戏运行 | `127.0.0.1:16384` |
| DeepSeek API | LLM 大脑 | `.env` DEEPSEEK_API_KEY |
| 通义千问-VL | VLM 感知 | `.env` VLM_API_KEY |
| GitHub | 代码仓库 | https://github.com/wusheng121/Arknights_agent |

---

## 测试命令

```bash
# 跑 sim 测试
python -m pytest tests/ -q

# 跑 1-7 sim (单关)
python -c "from src.sim.game_state import run_job; import json; ..."

# 跑全流程 (需要游戏运行)
python -m src.real_run --llm --stage 1-7 --fresh

# 跑 AI 主播模拟 (不需要游戏)
python -m src.streamer.streamer
```
