# AI 明日方舟主播 - 待办与规划

## 当前状态 (2026-09-17)

### 已完成

- **L1 通关验证**: 1-7 Stars=3 (煌单核), AT-7 Stars=3 (专家作业)
- **L2 知识库**: ArknightsGameData + 波次解析 + 敌人属性 + 干员特性 + RAG
- **sim 模拟器**: 67% 通过率 (13个bug修复, 含fragment串行/AUTO技能/atk_scale)
- **条件化作业**: kills/costs 条件化, 不用固定时间
- **自反思记忆 (P3)**: 真机结果→memory.py→自动promote原则
- **AI 主播框架**: EventBus + Commentator(LLM解说) + TTS(edge-tts+MCI) + 弹幕(mock)
- **BattleMonitor 安全网**: 实时截图+感知+应急干预
- **UI 导航器**: 界面检测(ToggleSettingsMenu 0.967) + 游戏启动
- **MAA 触控**: minitouch 模式 (与 MAA GUI 一致)
- **MAA 导航已解决**: StartUp(client_type=Official) + Fight(stage, times=1) → 导航到关卡 + 开路战斗
- **MAA Copilot**: Fight结束后游戏在formation界面, Copilot(filename, formation=True)直接接管
- **胜负检测**: UINavigator.detect_screen() 检测 results 界面 → Stars 模板匹配
- **方案B全流程**: StartUp → Fight → Copilot → Stars=3 通关 → P3记忆记录

### 后续优化方向

1. **省理智**: Fight 导航到编队界面后,在 FightBegin 前停止,直接接 Copilot,省掉6点理智
2. **升级 MAA + copilot_list**: 一步到位(导航+编队+自定义战斗),省掉 Fight 开路
3. **测 LLM 生成作业**: 找无专家作业的关卡,测试 LLM 管道→sim验证→Fight+Copilot 全流程
4. **启用 AI 主播**: 连接 streamer 模块,TTS解说+弹幕
5. **多关卡验证**: 测试 2-1/3-4/CE-6 等,验证 Fight 导航通用性
6. **sim 天赋补全**: 怒潮凛冬(溅射)/斩业星熊(坚忍) 失败,补全后通过率可提升

---

## 模块清单

| 模块 | 文件 | 状态 |
|------|------|------|
| LLM 管道 | `src/brain/pipeline.py` | OK |
| LLM 客户端 | `src/brain/llm_client.py` | OK |
| RAG 检索 | `src/data/rag_retriever.py` | OK |
| 专家作业爬取 | `src/data/expert_crawler.py` | OK |
| 离线蒸馏 | `src/data/pass1_annotate.py` + `pass2_aggregate.py` | OK |
| 统计模式 | `src/data/pattern_extractor.py` | OK |
| 干员特性 | `src/data/oper_profile.py` | OK |
| 地图信息 | `src/data/map_info.py` | OK |
| 波次解析 | `src/data/wave_parser.py` | OK |
| 敌人属性 | `src/data/enemy_lookup.py` | OK |
| 关卡工具 | `src/data/stage_util.py` | OK |
| 后处理 | `src/data/job_post_process.py` | OK |
| MAA 封装 | `src/game/maapy_client.py` | OK |
| TileCalc | `src/game/tile_calc.py` | OK |
| CV 感知 | `src/game/cv_perception.py` | OK |
| 技能检测 | `src/game/skill_detector.py` | OK |
| 安全网 | `src/game/battle_monitor.py` | OK |
| UI 导航 | `src/game/ui_navigator.py` | OK |
| sim 核心 | `src/sim/game_state.py` | OK (67%) |
| sim 数据 | `src/sim/data_loader.py` | OK |
| sim 范围 | `src/sim/range_calc.py` | OK |
| sim 记忆 | `src/sim/memory.py` | OK |
| sim 验证 | `src/sim/validate.py` | OK |
| sim 校准 | `src/sim/calibrator.py` | OK |
| 事件总线 | `src/streamer/event_bus.py` | OK |
| 解说生成 | `src/streamer/commentator.py` | OK |
| TTS 引擎 | `src/streamer/tts_engine.py` | OK |
| VTube | `src/streamer/vtube_controller.py` | stub |
| OBS | `src/streamer/obs_controller.py` | stub |
| 弹幕 | `src/streamer/danmaku_reader.py` | mock |
| 主控 | `src/streamer/streamer.py` | OK |
| 主入口 | `src/real_run.py` | OK |

---

## 环境配置

```
Python 3.14
DeepSeek API (DEEPSEEK_API_KEY in .env)
edge-tts (pip install edge-tts)
MAA v6.16.8 (C:\Users\slient\Downloads\MAA-v6.16.8-win-x64)
MAA 源码 (C:\demo\MaaAssistantArknights-dev-v2)
MuMu 模拟器 (127.0.0.1:16384, 1920x1080, Android 15)
ADB (C:\Program Files\Netease\MuMu\nx_main\adb.exe)
MAA 触控模式: minitouch
```

---

## 测试命令

```bash
# 跑测试
python -m pytest tests/ -q

# 跑全流程 (需要游戏运行)
python -m src.real_run --llm --stage 1-7 --fresh

# 跑 AI 主播模拟 (不需要游戏)
python -m src.streamer.streamer
```
