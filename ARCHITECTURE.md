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

## 10. 微型模拟器方案（Tier 1.5/2 的钥匙）

### 10.1 为什么需要模拟器

| 没有模拟器 | 有模拟器 |
|-----------|---------|
| 每条 lesson 真机跑一局（分钟级） | sim 跑 ms 级 → 1000x 加速 |
| 不能验证作业合理性 | deploy → step → 看结果 |
| 失败后只有屏幕截图，难定位根因 | 结构化事件轨迹，直指根因 |
| 不能测试替代方案 | 反事实查询："如果换位置会怎样?" |
| principles 无法验证 | sim 里跑胜率验证规则对不对 |

### 10.2 核心 6 模块

| 模块 | 简化建模 |
|------|---------|
| 网格 | tile 类型（近战/高台/阻挡），坐标 |
| 敌人 | hp/atk/def/速度/路径/是否存活；路径预定义不用寻路 |
| 干员 | hp/atk/def/范围(命中哪些 tile)/阻挡数/DP 费用/技能/朝向 |
| DP | 随时间回复，部署扣费 |
| 战斗循环 | 离散 tick（0.1s/0.5s 一帧） |
| 胜负 | 全波清完=赢；漏怪超阈值=输 |

### 10.3 核心循环

```
step(dt=0.1s):
  1. DP 回复
  2. 敌人沿路径移动；被阻挡数未满的干员挡住→停
  3. 干员攻击范围内的敌人（atk−def, min 1）
  4. 敌人攻击阻挡它的干员
  5. 结算死亡（敌人/干员）
  6. 敌人走到终点→扣血/漏怪
  7. 按波次时间轴刷新敌人
  8. 技能 SP 充能（INCREASE_WITH_TIME: +1/s, INCREASE_WHEN_ATTACK: +1/attack）
  9. 判胜负
```

### 10.4 LLM 可视化（结构化数据，非像素）

```json
// 决策点状态快照
{"tick":40, "dp":12, "lives":3,
 "enemies":[{"type":"源石虫","pos":[3,5],"hp":80,"dist_to_blue":5}],
 "operators":[{"name":"夜莺","pos":[2,5],"skill_sp":0,"skill_max_sp":8,
               "healing_targets":0}]}

// 事件轨迹
tick 16: 维什戴尔 skill_usage=1 自动开启(skill3弹药制)
tick 17: Skill action 关闭维什戴尔技能 (开启后1tick就关!)
tick 21: 逻各斯 Skill 执行但 SP=2/45 (未就绪)
tick 31: 夜莺 healing_targets=0 (范围覆盖不到友方!)
tick 40: 漏怪 (夜莺侧无阻挡)

// 失败根因总结
root_causes:
  1. 夜莺(2,5)治疗范围无目标 → 无作用
  2. 维什戴尔技能开启后1tick关闭 → 弹药浪费
  3. 逻各斯技能SP=2/45时执行 → 无效
suggestions:
  1. 夜莺应放(6,4)覆盖塞雷娅+逻各斯
  2. 维什戴尔去掉Skill action或加kills条件
  3. 逻各斯Skill加kills条件等SP充满
```

### 10.5 反事实查询（只有 sim 能做）

LLM 问 sim："如果夜莺放(6,4)会怎样?" → sim 跑一遍 → "治疗覆盖2人，0漏" → LLM 知道(6,4)更好。

### 10.6 数据需求（全部已有）

| 数据 | 来源 | 状态 |
|------|------|------|
| 网格/路径/波次 | level JSON + tile JSON | ✅ |
| 干员属性/范围/技能 | character_table + skill_table + battle_data | ✅ |
| 敌人属性/路径 | enemy_database + level routes | ✅ |
| 技能 SP 数值 | skill_table spData (spCost/spInit/spType) | ✅ 但未喂给 LLM |
| 技能效果数值 | skill_table blackboard | ✅ 但格式复杂，V2 再做 |

### 10.7 实现计划

**Phase 1: 最小模拟器（1-2 周）**
- AT-7 + 6 个干员
- V1 不做技能效果（只做普攻+阻挡+移动+DP+波次+胜负）
- `game_state.py` + `range_calc.py` + `data_loader.py` + `trace_summarizer.py`

**Phase 2: LLM + sim 集成**
- LLM 生成作业 → sim 验证 → 失败给根因 → LLM 修正

**Phase 3: 反事实查询**
- LLM 问 sim "如果换位置/技能/干员会怎样"

### 10.8 诚实的天花板

- 简化机制 → sim 策略可能 exploit 简化（sim 赢真机不赢）
- 需要定期"sim→真机"校准
- Tier 2 天花板：避免重复错 + 小范围改进，不是发明新策略

## 11. 干员信息喂入问题

### 11.1 当前 LLM 看到的 vs 没看到的

| 数据 | LLM 看到的 | LLM 没看到的（数据有但没喂） |
|------|-----------|------------------------|
| 攻击范围 | `范围大(19格5行)` 文字 | `[[0,2],[1,2],...,[3,-1]]` 具体 tile 坐标 |
| 技能充能 | `CD4s` 文本 | `spCost=50 spInit=40` 精确数值 |
| 技能持续 | `持续-1s` 文本 | `duration=25.0` 秒数 |
| SP 充能类型 | 无 | `INCREASE_WITH_TIME` / `INCREASE_WHEN_ATTACK` |
| 技能效果 | `攻击力+X` X 没填 | `blackboard: [{key:"atk", value:2.0}]` |
| 干员属性 | 无 | `HP2500 ATK800 DEF200 RES0` |

### 11.2 导致的问题

1. 夜莺无作用：LLM 只看到"范围中(12格3行)"，无法计算从(2,5)朝 Right 是否覆盖友方
2. 逻各斯技能没好：LLM 只看到 `CD45s`，不知道 `spInit=30`（还需 15 SP ≈ 15 秒）
3. 维什戴尔秒关：LLM 看到 `持续-1s` 但不知道 -1 意味着"弹药制/可手动关闭"

## 12. AI 游戏方案调研与明日方舟适配性分析

### 12.1 调研项目

| 项目 | Stars | 架构 | 参考价值 |
|------|-------|------|----------|
| Tencent/GameAISDK | 2680 | AIClient→ManageCenter→AgentAI(DQN/IM)+ImgProc | 感知/动作框架参考，但 RL 核心不适用 |
| lmgame-org/GamingAgent | 971 (ICLR 2026) | Perception→Memory→Reasoning 逐帧循环 | 架构理念参考，但逐帧循环不适合方舟 |
| git-disl/awesome-LLM-game-agent-papers | 953 (ACM CSUR) | 综述：44篇记忆+34篇自我改进 | 趋势参考：技能库/信念记忆/回顾反馈 |
| MaaAssistantArknights/MaaAI | - | 深度学习模型（MobileNetv4/YOLOv8/CNN） | 已有方舟专用感知模型，无需重建 |
| MaaAssistantArknights (MAA) | 14k+ | Copilot 协议+BattlefieldMatcher/Classifier | 核心基础设施，已有完整战斗感知 |

### 12.2 明日方舟游戏特性

| 特性 | 说明 | 对 AI 方案影响 |
|------|------|---------------|
| 可暂停+子弹时间 | 下达命令时 1/5 速度 | 不需要实时反应，逐帧 VLM 循环是浪费 |
| 拼图式策略 | "有限解，重分析" | 一次性规划比逐步反应更合适 |
| 格子部署 | 只能放在特定格子 | tile_calc.py 已解决，不需要 VLM |
| DP 费用管理 | 费用不够要等 | MAA Copilot 支持 costs 条件 |
| 技能时机 | SP 充满才能开 | MAA Copilot 支持 kills 条件替代时间 |
| 敌人血量不可见 | 只有干员血条 | 无法精确感知敌人，sim 更可靠 |
| 游戏自带录像回放 | 3星通关后自动完成 | MAA Copilot 是这个机制的延伸 |

### 12.3 MAA 已有的战斗感知能力

| 感知模块 | 模型 | 耗时 | 能力 |
|----------|------|------|------|
| BattlefieldClassifier | MobileNetv4 Small (9M) | <1ms | 技能就绪检测（有/无/可关闭三分类） |
| BattlefieldClassifier | MnistSimpleCNN (18M) | ~20ms | 干员朝向检测（左/右/上/下四分类） |
| BattlefieldDetector | YOLOv8 N (12M) | ~50ms | 干员位置检测（血条） |
| BattlefieldMatcher | OCR+模板匹配 | - | 击杀数/DP费用/部署面板/速度按钮/暂停按钮 |

MAA C API 导出：
- `AsstAsyncClick(x, y, block)` — 异步点击
- `AsstAsyncScreencap(block)` — 异步截图
- `AsstGetImage(buff, size)` / `AsstGetImageBgr(buff, size)` — 获取截图

MAA Copilot 条件系统：
- `kills`: 等击杀数达到 N
- `costs`: 等费用达到 N
- `cost_changes`: 等费用变化量达到 N
- `cooling`: 等 CD 中干员数达到 N
- `elapsed_time`: 等时间达到 N 毫秒
- `pre_delay` / `post_delay`: 前后延时
- `timeout`: 超时放弃

### 12.4 方案适配性评估

| 外部方案 | 适合方舟? | 原因 |
|----------|----------|------|
| GamingAgent 逐帧循环 | ❌ | 游戏可暂停，不需要每帧感知，浪费 VLM token |
| VLM 实时感知 | ❌ | MAA 已有深度学习感知模型，不需要 VLM 做感知 |
| 脚手架叠加网格 | ❌ | tile_calc.py 精确算坐标，不需要 VLM 理解棋盘 |
| 信念记忆 | ❌ | 敌人血量不可见但 sim 有精确数据，不需要"猜测" |
| 条件化作业生成 | ✅ | Copilot 支持 kills/costs 条件，比固定时间更鲁棒 |
| sim 验证 | ✅ | 无法从画面感知敌人状态，sim 是唯一可靠验证 |
| MAA 感知集成 | ✅ | 利用 MAA 已有模型，无需重建 |
| 安全网干预 | ✅ | sim 通过后真机执行，出意外用 MAA 感知做应急 |
| 技能库(Voyager) | ✅ | 专家作业+原则是静态版，可动态化 |

### 12.5 最终方案：增强条件化作业 + sim 验证 + 安全网

```
                    ┌─ 有专家作业 ──→ 直接使用 ────────────────┐
                    │                                          │
关卡数据 → LLM 管道 ─┤                                          │
                    │                                          ├──→ MAA 执行 → 胜负检测
                    └─ 无专家作业 →                              │
                         ① LLM 生成条件化作业                    │
                         (用 kills/costs 条件, 非固定时间)        │
                                ↓                               │
                         ② sim 验证可行性                        │
                                ↓                               │
                         ③ LLM 修正 (max 2轮)                   │
                                ↓                               │
                         ④ 记忆记录 ───────────────────────────→│
                                                                │
                    ┌── 安全网监控 (仅失败时) ←─────────────────┘
                    │   MAA BattlefieldMatcher → kills/costs/skill_ready
                    │   如果检测到异常 → 应急撤退/技能/部署
                    └── 记忆记录失败原因
```

### 12.6 实施优先级

| 优先级 | 任务 | 工作量 | 价值 |
|--------|------|--------|------|
| P0 | 条件化作业生成（LLM prompt 改为输出 kills/costs 条件） | 小 | 高 |
| P0 | sim 验证条件可行性 | 中 | 高 |
| P1 | MAA Python API 截图集成 | 中 | 中 |
| P1 | 安全网应急干预 | 中 | 中 |
| P2 | sim-to-real 校准 | 大 | 高 |
| P3 | Tier 2 记忆改进 | 小 | 中 |

## 13. AI 主播兼容性分析

### 13.1 AI 主播需要的核心能力

| 能力 | 说明 | 当前状态 |
|------|------|----------|
| 游戏操作 | 打关卡、编队、部署、技能 | ✅ 已有（MAA Copilot + LLM 管道） |
| 实时感知 | 知道战斗中发生了什么 | ⚠️ 安全网感知模块（P1）可复用 |
| 解说生成 | 根据游戏状态生成评论文本 | ❌ 需新建 |
| TTS | 文字转语音 | ❌ 需集成 |
| VTube | 虚拟形象驱动 | ❌ 需集成 |
| OBS | 直播推流 | ❌ 需集成 |
| 弹幕交互 | 读弹幕、回复 | ❌ 需新建 |

### 13.2 方案对 AI 主播的支撑度

**✅ 直接支撑**：
- 游戏操作：LLM 管道 + MAA 执行 = 自动打关卡
- 作业数据：copilot_job.json 里的 actions 自带 doc 字段，可作为解说文本基础
- sim 事件轨迹：sim 的 event_log（部署/技能/击杀/漏怪）可作为解说素材

**⚠️ 需要扩展**：
- 实时感知：安全网模块（P1）的 MAA BattlefieldMatcher 输出 → 同时喂给解说生成器
  - kills 变化 → "已经击杀 15 个敌人！"
  - 部署事件 → "维什戴尔部署在 8 号位，朝下输出！"
  - 技能就绪 → "维什戴尔技能好了，准备爆发！"
  - 干员血量低 → "不好，塞雷娅血量危险！"
  - 漏怪 → "漏了一个！情况不妙！"
- sim 预判 → 解说预告："下一波有精英怪，建议提前部署"

**❌ 需要新建**：
- 解说生成器：LLM 输入(sim事件 + MAA感知 + 弹幕) → 输出(解说文本)
- TTS 集成：调用 edge-tts / GPT-SoVITS / VITS
- VTube 集成：通过 VTube Studio API 或 VRM 驱动
- OBS 集成：通过 obs-websocket 控制场景切换
- 弹幕交互：B站/抖音弹幕 API → LLM 回复

### 13.3 AI 主播完整架构

```
┌─────────────────────────────────────────────────────────────┐
│                        AI 主播控制中心                        │
├──────────┬──────────┬──────────┬──────────┬────────────────┤
│ 游戏操作  │ 实时感知  │ 解说生成  │ TTS+VTube │ 弹幕交互       │
│          │          │          │          │                │
│ LLM 管道 │ MAA 感知  │ LLM 解说  │ edge-tts  │ 弹幕 API      │
│ MAA 执行  │ sim 事件  │ 事件→文本 │ VTube API │ LLM 回复      │
│ sim 验证  │ 安全网    │ 弹幕→回复 │ OBS ws    │ 过滤+排程      │
├──────────┴──────────┴──────────┴──────────┴────────────────┤
│                      事件总线 (Event Bus)                    │
│  事件类型: deploy/skill/kill/leak/hp_low/battle_start/end   │
│  来源: MAA callback + sim event_log + 安全网检测             │
└─────────────────────────────────────────────────────────────┘
```

### 13.4 关键结论

**当前方案（条件化作业+sim验证+安全网）能支撑 AI 主播吗？**

**能，但需要扩展。** 核心原因：

1. **安全网感知模块是 AI 主播的基础**：计划中的 P1 安全网模块（MAA BattlefieldMatcher）本身就是实时游戏状态感知。AI 主播的解说生成器直接消费同一个事件流。

2. **sim 事件轨迹是解说素材**：sim 跑出的 event_log（部署/技能/击杀/漏怪）可作为预解说。真机执行时，对比 sim 预判 vs 实际结果，生成"翻车了"或"比预期还顺利"的解说。

3. **作业 doc 字段是字幕**：Copilot 协议的 `doc` 和 `doc_color` 字段天然适合做字幕/解说。

4. **需要新建的部分**：
   - 事件总线（Event Bus）：统一 MAA callback + sim 事件 + 安全网检测
   - 解说生成器：LLM 输入事件流 → 输出解说文本
   - TTS + VTube + OBS 集成
   - 弹幕 API + LLM 回复

5. **实施路径**：
   - Phase 1 (当前): 游戏操作 + sim 验证 + 记忆 → 自动通关 ✅
   - Phase 2: 安全网感知 + 事件总线 → 实时状态感知
   - Phase 3: 解说生成 + TTS → 有声音的 AI 主播
   - Phase 4: VTube + OBS + 弹幕 → 完整直播体验
