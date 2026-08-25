# AI 明日方舟主播 - 架构设计文档

## 1. 核心理念

**LLM 的价值不是"从零规划"，而是"理解攻略→适配当前账号→生成可执行作业"。**

纯 LLM 规划不稳定（AT-7 测试中反复出现：同路径两个重装、干员位置打不到人、技能选错、不撤先锋）。
根本原因：LLM 没有"专家知识"，它在猜。

解决方案：**RAG（检索增强生成）**——从 prts.wiki 攻略 + MAA 社区作业 JSON 中检索专家知识，喂给 LLM，让它基于"专家建议"而非"猜测"做决策。

## 2. 推荐架构

```
用户输入关卡 (如 AT-7)
   │
   ├── RAG 检索①: prts.wiki 攻略文本
   │   → "推荐干员:桃金娘/塞雷娅/维什戴尔..."
   │   → "银灰右路高台朝下,塞雷娅蓝门旁地面朝右"
   │   → "先下先锋回费,费用到20下塞雷娅"
   │
   ├── RAG 检索②: MAA Copilot 作业 JSON
   │   → {"name":"塞雷娅","location":[2,3],"direction":"Right","costs":22}
   │   → {"type":"Retreat","name":"桃金娘","kills":15}
   │   → 结构化专家演示,已验证可通关
   │
   ├── LLM: 攻略 + 作业 + 干员特性 + 地图信息 → 输出部署决策 JSON
   │   → 适配当前账号可用干员(攻略推荐银灰但用户没有→换成拉普兰德)
   │   → 适配当前关卡状态(费用/波次/蓝门位置)
   │
   ├── 启发式约束(合法性校验):
   │   → 干员在可用列表? 格子没被占? 朝向合理? 费用够?
   │
   ├── 后处理(安全兜底):
   │   → 蓝门覆盖检查 / 撤先锋时机 / 位置修正 / 方向计算
   │
   └── MAA Copilot 执行 + 缓存(同关卡复用)
```

## 3. 方案对比与决策

| 方案 | 可行性 | 决策 | 理由 |
|------|--------|------|------|
| A. RAG (wiki + 作业 JSON) | ⭐⭐⭐⭐⭐ | ✅ 采用 | 结构化数据,免 CV,LLM 理解攻略文本是强项 |
| B. 视频 CV 提取 | ⭐⭐ | ❌ 砍掉 | MAA 作业 JSON 是更好的"专家演示",免训 YOLO/OCR |
| C. 微调训练 | ⭐ | ❌ 砍掉 | RAG 已注入知识,微调给"风格"不给"知识",成本不划算 |

### 为什么砍掉 B（视频 CV）
- MAA 社区已有成千上万份 Copilot 作业 JSON，包含 deploy 时间/位置/朝向/技能时机
- 这是明日方舟版的 replay，比视频省去 CV 这层苦力
- B 的"数据"直接拿 MAA 作业 JSON 就行，整个方案 B 塌缩进 A

## 4. 可行性逐 Component 分析

### 4.1 RAG over prts.wiki 攻略文本

| 维度 | 评估 |
|------|------|
| 数据获取 | ⭐⭐⭐⭐⭐ MediaWiki API 无反爬,已有 `prts_crawler.py` 基础 |
| 数据质量 | ⭐⭐⭐⭐ 自然语言攻略,含推荐干员/位置/策略 |
| 检索方式 | ⭐⭐⭐⭐⭐ 向量化(Embedding)+ 关卡代码索引 |
| LLM 理解 | ⭐⭐⭐⭐⭐ LLM 把"银灰右路高台朝下"解析成 {干员,格子,朝向} |

**实现**:
```
prts.wiki → 爬取关卡攻略页面 → 切块 → Embedding 向量化 → 存入向量库
用户输入关卡 → 检索 top-k 攻略片段 → 喂给 LLM
```

**爬取范围**: 先聚焦前 N 个常用关卡 MVP（主线 1-7~1-12, 常用活动关），验证效果后扩展。

### 4.2 RAG over MAA Copilot 作业 JSON

| 维度 | 评估 |
|------|------|
| 数据获取 | ⭐⭐⭐⭐⭐ MAA 安装目录 `resource/copilot/` + 社区作业库 |
| 数据质量 | ⭐⭐⭐⭐⭐ 结构化 JSON,已验证可通关,含精确位置/朝向/时机 |
| 检索方式 | ⭐⭐⭐⭐ 按 stage_name 索引 + 向量相似度 |
| LLM 利用 | ⭐⭐⭐⭐⭐ LLM 直接读 JSON,适配可用干员 |

**MAA 内置作业**: 74 份（主要为 SSS 关卡 + 信用战）
**社区作业**: 需从 MAA 社区/GitHub 爬取,或自己手写起步

**实现**:
```
MAA 作业 JSON → 解析(stage_name/opers/actions) → 按 stage_name 索引
用户输入关卡 → 检索同关卡的作业 JSON → 喂给 LLM 作为"专家演示"
LLM: "这是 AT-7 的专家作业,用户可用干员是 [...],适配并输出新作业"
```

**关键**: 作业 JSON 是宝藏——结构化、已验证,比 wiki 文本质量更高。

### 4.3 LLM 选干员 + 技能 + 位置

| 维度 | 评估 |
|------|------|
| 输入质量 | ⭐⭐⭐⭐ RAG 注入攻略+作业,不再猜 |
| 输出可靠性 | ⭐⭐⭐⭐ 强制 JSON 输出 + 合法性校验兜底 |
| 适配能力 | ⭐⭐⭐⭐ LLM 能适配(攻略推荐银灰但用户没有→换拉普兰德) |
| 速度 | ⭐⭐⭐⭐ DeepSeek ~2-3s/次,4 步管道 ~10s |

**LLM 选择**: DeepSeek API（便宜、中文好、已验证）

### 4.4 启发式 + 后处理

| 维度 | 评估 |
|------|------|
| 稳定性 | ⭐⭐⭐⭐⭐ 确定性规则,最稳的一环 |
| 已实现 | 方向计算(覆盖最大化)、蓝门覆盖检查、撤先锋、位置修正 |
| 待实现 | 干员合法性校验、格子占用检查、朝向合理性 |

### 4.5 MAA Copilot 执行 + 缓存

| 维度 | 评估 |
|------|------|
| 执行可靠性 | ⭐⭐⭐⭐ MAA deploy_oper 闭环,已验证 |
| 缓存复用 | ⭐⭐⭐⭐⭐ 同关卡直接回放,省 LLM 调用 |

## 5. 关键认知（决定成败）

### 5.1 攻略→动作的映射是 LLM 价值所在
wiki 是自然语言（"银灰右路高台朝下"），LLM 把它解析成结构化 `{干员, 格子, 朝向}`——这是 LLM 比纯脚本强的地方。

### 5.2 合法性校验层必须有
LLM 会输出非法动作（干员不可用/格子被占/朝向不合理）。启发式+后处理是兜底,别省。

### 5.3 状态对齐
攻略假设理想情况，实战要按当前可用干员/DP/敌人波次适配——LLM 在 prompt 里带当前状态,让它适配而非照抄。

### 5.4 新关卡/无攻略回退
RAG 查不到时退到 LLM 通用推理 + 启发式（当前的多步管道），不卡死。

### 5.5 作业 JSON 是宝藏
MAA 社区作业库可能比 wiki 文本质量更高（结构化、已验证），值得作为主检索源之一。

## 6. 解决之前的游戏理解问题

| 问题 | 当前(纯 prompt) | RAG 方案 |
|------|-----------------|---------|
| 两个重装同一路径 | LLM 不知道不该这样 | wiki 攻略明确写"塞雷娅放蓝门旁,星熊放右侧" |
| 晓歌位置打不到人 | LLM 猜位置 | 作业 JSON 有精确位置 `[3,5]` |
| 桃金娘选错技能 | LLM 凭预训练知识猜 | wiki 攻略写"桃金娘带一技能" |
| 不撤先锋 | 后处理强制撤退 | 作业 JSON 有 `Retreat` action + kills 时机 |
| 蓝门没人守 | 后处理移动干员 | wiki 攻略描述每个蓝门的防守方案 |

## 7. 实现计划

### Phase 1: RAG 基础设施 (2-3 天)

- [ ] 向量库搭建 (用 ChromaDB 或 FAISS,本地无需服务器)
- [ ] prts.wiki 攻略爬取 + 切块 + 向量化
  - 先爬主线 1-7~1-12 + AT-7 等常用关卡
  - 切块策略: 按关卡分段,每段含推荐干员/位置/策略
- [ ] MAA 作业 JSON 解析 + 索引
  - 解析 `resource/copilot/` 下所有作业
  - 按 stage_name 索引
- [ ] 检索接口: `retrieve_guides(stage_code) → (wiki_text, job_json)`

### Phase 2: LLM 适配层 (1-2 天)

- [ ] 新 prompt: 攻略 + 作业 + 干员特性 + 地图 → 适配输出
  - "这是 AT-7 的专家攻略和作业。用户可用干员是 [...]。适配并输出新作业。"
  - "攻略推荐银灰但用户没有银灰,选择相似角色(拉普兰德)替代"
- [ ] 合法性校验层: 干员在可用列表? 格子没被占? 朝向合理?
- [ ] 集成到多步管道(Step 1-2 注入 RAG 检索结果)

### Phase 3: 启发式增强 (1 天)

- [ ] 强化后处理(已有基础):
  - 蓝门覆盖检查(排除被撤退先锋) ✅ 已实现
  - 撤先锋时机 ✅ 已实现
  - 方向计算(覆盖最大化) ✅ 已实现
  - 位置修正(高台/地面类型匹配) ✅ 已实现
  - 新增: 同路径不重复放阻挡
  - 新增: 干员位置必须在敌人路径附近

### Phase 4: 实时感知 + ADB 控制 (后续)

- [ ] ADB 执行层(deploy+方向+撤退+技能+验证)
- [ ] CV 感知层(敌人检测+技能就绪+战斗结束)
- [ ] GameState 持续跟踪
- [ ] 闭环主循环(感知→决策→执行→验证)

### Phase 5: AI 主播 (后续)

- [ ] TTS 语音 + VTube 表情 + OBS 推流 + 弹幕互动

## 8. 待确认

1. **prts.wiki 爬取范围**: 先聚焦前 N 个常用关卡 MVP? ✅ 已完成（255 关卡 1365 份作业）
2. **MAA 作业 JSON 来源**: MAA 内置 74 份(SSS 为主),需要从社区爬取更多? ✅ 已从 prts.plus API 爬取
3. **LLM 选择**: DeepSeek API 起手(已验证,便宜),后续可换? ✅ DeepSeek
4. **向量库**: ChromaDB(本地,简单) vs FAISS(轻量) vs 云端? ✅ ChromaDB 已安装

## 9. 实时控制方案（待实现）

### 9.1 问题背景

当前 MAA Copilot 模式是开环——执行专家作业的所有 actions（Deploy/Skill/Retreat），无法在 action 之间插入检测/调整。

**具体问题**：弹药制技能（如予愿安洁莉娜 skill=3）的 `Skill kills:10` action 是"提前关闭技能"，但如果弹药在 kills=10 前已打完，MAA 仍会点击干员尝试关闭→只是选中干员→游戏变慢。

### 9.2 方案：MAA Copilot + Python 并行监控

**不移除 Skill action**，让 MAA Copilot 执行全部 actions（保证专家作业完整性）。Python 并行监控技能状态，记录分析（后续可实现主动干预）。

```
MAA Copilot 线程: 编队→开始战斗→Deploy→Skill→Retreat→SkillDaemon→完成
     ↕ 并行
Python 线程: get_image(MAA Minicap 5ms) → detect_skill_state(CV 50ms) → 记录/决策
```

### 9.3 技能状态检测（三种状态）

已实现 `src/game/skill_detector.py`：

| 状态 | 检测方式 | 含义 |
|------|---------|------|
| `not_ready` | 模板都不匹配 | 技能没好(SP 未充满) 或 技能已结束 |
| `ready` | `BattleSkillReady.png` 匹配 | 技能好了但没开(SP 满,未激活) |
| `active` | `BattleSkillStopOnClick-TopView.png` 匹配 | 技能开启中(正在释放) |

### 9.4 决策逻辑（已实现）

```python
should_execute_skill_action(action_type, skill_state, is_ammo_skill):
  # 弹药制技能 (Skill=关闭):
  #   active → 执行关闭 ✅ (技能还在开,需要提前关)
  #   not_ready → 跳过 ❌ (技能已结束,不需要关)
  #   ready → 跳过 ❌ (技能没开,不需要关)
  
  # 普通技能 (Skill=激活):
  #   ready → 执行激活 ✅ (技能好了,可以开)
  #   active → 跳过 ❌ (已经在开了)
  #   not_ready → 跳过 ❌ (没好,等等)
```

### 9.5 需要的 MAA Python API 绑定

当前 `asst.py` 只绑定了 `get_image()`。需要增加绑定（无 C++ 改动）：

| C API 函数 | Python 方法 | 用途 | 状态 |
|------------|-----------|------|------|
| `AsstAsyncClick(handle, x, y, block)` | `Asst.click(x, y)` | MAA Minitouch tap (<1ms) | 待绑定 |
| `AsstGetImageBgr(handle, buff, size)` | `Asst.get_image_bgr()` | BGR 截图（OpenCV 格式） | 待绑定 |
| `AsstAsyncScreencap(handle, block)` | `Asst.screencap()` | 异步触发截图 | 待绑定 |

**注意**：MAA C API 没有导出 `AsstSwipe`。如需 Python 控制 swipe（部署干员），需要：
- 路径 A：修改 MAA C++ 源码加 `AsstAsyncSwipe` → 重编译 MaaCore.dll
- 路径 B：Python 移植 Minitouch 文本协议（socket 通信，独立于 MAA DLL）

当前方案不涉及 swipe——Deploy 由 MAA Copilot 执行（可靠），Python 只做 tap（技能/撤退）和截图监控。

### 9.6 实现步骤

**Step 1**：绑定 MAA Python API（`asst.py` 加 3 个方法）
- 无 C++ 改动，无重编译
- 立即获得 MAA 级 tap + 截图能力

**Step 2**：实时监控循环（Python 并行运行）
```python
# MAA Copilot 执行专家作业（包括 Skill action）
await client.append("Copilot", {"filename": job_path, ...})
await client.start()

# Python 并行: 监控技能状态（记录分析,不干预）
while client.running():
    img = get_image_bgr()  # MAA Minicap 5ms
    for oper in deployed_opers:
        pos = tile_calc.get_tile_screen_pos(oper.row, oper.col)
        state = detect_skill_state(img, pos)
        log("  %s skill_state=%s" % (oper.name, state))
    await asyncio.sleep(1)
```

**Step 3**（后续）：主动干预
- 当检测到 Skill action 即将执行但技能状态显示不需要 → 用 `AsstStop()` 暂停 MAA
- 用 `AsstAsyncClick()` 手动执行/跳过技能
- 再用 `AsstStart()` 恢复 MAA 执行

### 9.7 为什么不直接移除 Skill action

用户明确指出：移除 Skill action 可能导致失败。专家作业作者设计的 Skill 时机是经过验证的，移除后：
- 弹药制技能无法提前关闭 → 可能浪费弹药 → 后续波次无法应对
- 普通技能无法在特定时机激活 → 战术节奏被打乱

正确做法：**保留全部 actions，检测技能状态用于分析和未来的主动干预**。

## 10. 数据源更新

| 数据源 | 用途 | 位置 | 状态 |
|--------|------|------|------|
| MAA 安装 | 执行/感知/模板 | `C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\` | ✅ |
| MAA 源码 | 参考/编译 | `C:\demo\MaaAssistantArknights-dev-v2\` | ✅ |
| ArknightsGameData | 游戏数据 | `data/gamedata/` | ✅ |
| prts.wiki | 补充数据 | `https://prts.wiki/api.php` | ✅ |
| **prts.plus API** | **专家作业** | `https://prts.maa.plus/copilot/query` | ✅ |
| **本地专家作业** | **RAG 检索** | `data/expert_jobs/` (255 关卡 1365 份) | ✅ |
| MuMu 模拟器 | 游戏运行 | `127.0.0.1:16384` | ✅ |
| DeepSeek API | LLM 大脑 | `.env` DEEPSEEK_API_KEY | ✅ |
| 通义千问-VL | VLM 感知 | `.env` VLM_API_KEY | ✅ |
| ChromaDB | 向量库 | 本地 | ✅ 已安装 |
