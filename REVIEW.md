# `ai_streamer_plan.md` 方案评审

> 评审日期:2026-08-17
> 评审对象:`ai_streamer_plan.md`(AI 虚拟主播 + 明日方舟 Agent 推荐方案,作者XXX)
> 约束:目标=分析评估;算力=8–12GB(已决策本地 AI 全走云 API);大脑=DeepSeek API;平台=B站
> 评审依据:方案文档 + 联网核实 MAA 主仓库 README/集成文档/战斗流程协议/回调消息协议、DeepSeek API 官方文档、MaaAI 仓库、国家《人工智能生成合成内容标识办法》

---

## 一、地基核实(已联网查证)

### 1. MAA 地基成立,且 Phase 3 命门已解除(关键)

- MAA(MaaAssistantArknights)22.6k★,活跃维护,支持作业 JSON 自动抄作业(Copilot)、肉鸽全自动、干员/练度识别、基建换班等。方案所述能力属实。
- **纠偏:不存在独立的 `MaaPy` 仓库**(该 URL 直接 Transport error)。Python binding 在 MAA 主仓库内 `src/Python/asst.py`,集成示例为 `src/Python/sample.py`。
- MAA 关键协议(Phase 3 实现必读):自动战斗协议 `copilot-schema.html`、集成文档 `integration.html`、回调 `callback-schema.html`。
- **grounding 已被 MAA 解决**:`Arknights-Tile-Pos`(格子映射)+ `MaaTouch/Minitouch`(ADB 触控)封装了格子→坐标→触控,方案第六节该风险从高降为低。

### 2. DeepSeek API 选型成立,2026 年已升级

- 当前模型 `deepseek-v4-flash` / `deepseek-v4-pro`,原生支持 **Tool Calls / JSON Output / Thinking Mode + reasoning_effort**,兼容 OpenAI/Anthropic SDK。游戏决策可开 thinking,方案未提,是可利用增量。

---

## 二、技术选型修订(云 API 优先)

**决策**:本地 AI 推理/训练全部走云 API,不依赖 8–12GB 本地显存。本地机器只跑编排 + ADB + OBS + VTube Studio + 轻量过滤。

| 环节 | 原方案 | 修订后 |
|---|---|---|
| 大脑 LLM | DeepSeek API | 不变 |
| TTS MVP | edge-tts | 不变(免费起步) |
| TTS 音色克隆(Phase 2) | GPT-SoVITS **本地** | **云端**:CosyVoice API / 火山 / minimax,不本地训 |
| VLM 视觉理解(Phase 3) | Qwen-VL **本地** | **云端 VLM API**:通义千问-VL / Gemini / GPT-4o |
| 微调人设(Phase 2) | LLaMA-Factory 本地微调 Qwen2.5-7B | **弱化/推迟**:先靠 system prompt + RAG + 少样本;确要微调走 autodl 云 GPU |
| 本地机器 | 需要 8–12GB 独显 | **独显非必需**:轻量主机即可 |

### 连带新增风险(云 API 化引入)

1. **成本**:直播长时段 LLM+TTS+VLM 三路云调用累计费用需估;Phase 3 决策频繁调 VLM 尤甚——简单局面走规则,难局才上云。
2. **可用性**:直播中 API 抖动=直播事故。每路须做超时/重试/降级(见第八节框架)。
3. **延迟**:云 VLM 往返含网络,需实测并纳入「决策可暂停」的节目编排。
4. **隐私**:弹幕+游戏截图上云,注意不传敏感信息。

---

## 三、可行性评估(按 Phase)

| Phase | 算力影响 | 可行性 | 备注 |
|---|---|---|---|
| 0 环境打通 | 无 | 高 | 工期偏乐观,首次跑通实际 3–5 天而非 1–2 天 |
| 1 Streamer MVP | 无(全云) | 高 | edge-tts + DeepSeek + VTube |
| 2 人设+记忆 | 云化后无本地瓶颈 | 中 | 见下「Phase 2 修订」 |
| 3 玩方舟 | 小 | **高(命门已解除)** | 见第八节 SingleStep 闭环;VLM 云延迟需实测 |
| 4 运营 | 无 | 高 | — |

### Phase 2 修订(与方案第七节自评对齐)

方案第七节自陈「别一上来卷模型,80% 力气在人设和记忆」,与 Phase 2 把「微调 Qwen」列为核心自相矛盾。修订为主线:DeepSeek API + 精心 system prompt + RAG(Chroma:直播日志 + lorebook + 跨场召回)+ 少样本;微调挪 Phase 4。瓶颈从「本地算力」转为「人设语料质量 + RAG 召回质量」。

---

## 四、风险评审(对方案第六节的纠偏/补充)

1. **[已解除] MaaPy 动态喂入**——原评「致命未验证」,现核实 MAA 原生支持 `SingleStep` 单步任务,可逐步喂 action;`AsstSetTaskParams` 支持运行时改参;`AsstCallback` 回调闭环可感知每步结果再决策。详见第八节-1。残余风险仅剩「Copilot 单步回调字段文档过时,需读 `AsstProxy.cs` 源码确认战局状态回调」。
2. **[降级] 动作 grounding**——MAA 已封装,高→低。
3. **[保留+细化] 感知语义鸿沟**:MAA CV 读的是特征点(干员头像/费用/HP),不便直接喂 LLM。但 **MaaAI 项目正好补这块**(OCR/技能就绪/血条/朝向 CV 模型,见第八节-2);仍需云 VLM 补「敌方波次/威胁方向」等高层语义。感知融合应为「MAA 结构化 + MaaAI 细粒度 + 云 VLM 语义描述」三路喂 LLM,方案只说「VLM 补」未设计融合。
4. **[新增] 编排调度缺口**:边打边解说/被弹幕干扰/输了吐槽是节目效果,但没设计调度策略(玩游戏时弹幕来了回不回?暂停多久?谁抢占?)。需在 Phase 1 编排层定义优先级/抢占/排队。
5. **[细化] 延迟分层**:聊天走流式 LLM+流式 TTS 压到 2s 内;游戏决策可暂停 5–15s 可接受,但须配「思考旁白」把等待变节目效果。
6. **[前置·部分已明] 合规**:《人工智能生成合成内容标识办法》2025-09-01 已施行,《人工智能拟人化互动服务管理暂行办法》2026-07-15 已施行(见第八节-3)。AI 实时生成内容输出审核难点在「审核延迟破坏互动」:建议输入弹幕过滤(关键词+LLM 判定)+ 输出轻量分类器+敏感词,深度审核异步。

---

## 五、改进建议(按优先级)

- ~~P0:验证 MaaPy 动态喂入~~ **已完成,命门解除**。
- ~~P0:调研 MaaAI~~ **已完成,感知层可复用**。
- ~~P0:前置研究 B站合规~~ **已完成,见第八节-3**。
- ~~P0:三路云调用降级框架~~ **已设计,见第八节-4**。
- **P1**:Phase 0 用 `SingleStep` 跑通「SingleStep stage→start→action×N + 回调闭环」最小 demo,确认战局状态回调字段(读 `AsstProxy.cs`)。
- **P1**:细化编排层调度(聊天/玩法优先级、抢占、暂停、思考旁白)。
- **P1**:设计「MAA 结构化 + MaaAI 细粒度 + 云 VLM 语义」三路感知融合数据结构喂 LLM。
- **P2**:Phase 2 弱化微调、挪 Phase 4;Phase 0 工期改 3–5 天;Phase 3 工期警惕翻倍(8–16 周)。
- **P2**:合规落地——直播画面叠加显著「AI 生成」标识(虚拟场景起始+持续)+ 流元数据隐式水印;DeepSeek 备案号公示;避开「虚拟伴侣/未成年人」叙事(应对拟人化互动办法)。

---

## 六、待办/待确认

- [x] MaaPy 是否支持运行时动态喂动作 → **支持,SingleStep**
- [x] MaaAI 项目现状与可复用性 → **感知层 CV 模型可复用,无 LLM**
- [x] B站 AIGC / AI 主播合规政策 → **国家办法已施行,B站细则文本待人工核实**
- [ ] 本地机器是否仍保留独显预算(云化后倾向轻量主机,待拍板)
- [ ] Phase 2 弱化微调、微调挪 Phase 4 是否认可
- [ ] 三路云调用成本预估(长时段累计)
- [ ] DeepSeek 生成式 AI 服务备案号公示(需查 DeepSeek 公开备案信息)
- [ ] Copilot 单步战局状态回调字段(读 MAA `src/MaaWpfGui/Main/AsstProxy.cs`)

---

## 七、P0 调研结论(2026-08-17 执行)

### 1. MaaPy 动态喂入——命门解除

MAA 集成文档确认,其任务接口(`AsstAppendTask` / `AsstSetTaskParams` / `AsstCallback`)原生支持「实时单步喂入」:

- **`SingleStep` 单步任务**(集成文档明列):`type:"copilot"`,`subtask` 三选一:
  - `stage` — 设置关卡名(`details:{stage:"1-7"}`)
  - `start` — 开始作战
  - `action` — **单步作战操作**,`details` 为战斗流程协议中的单个 action,如 `{name:"史尔特尔",location:[4,5],direction:"左"}`
- **action 格式**(见 copilot-schema):`type`(Deploy/Skill/Retreat/SpeedUp/BulletTime/SkillUsage/Output/SkillDaemon/MoveCamera/ResetStopWatch)+ 等待条件 `kills/costs/cost_changes/cooling/time_elapsed`(且关系)+ `name/location/direction` + `pre_delay/post_delay`。结构化、字段语义清晰,**LLM 友好**。
- **运行时改参**:`AsstSetTaskParams(handle, taskId, params)`——未标「不支持运行中设置」的字段都支持实时修改。
- **回调闭环**:`AsstCallback` 回传 `SubTaskStart/SubTaskCompleted/SubTaskExtraInfo/SubTaskError`,可感知每步执行结果,喂下一步 → 形成「感知→决策→执行→回调」闭环。
- **暂停部署**:实例选项 `DeploymentWithPause` 可「暂停下干员」,正好配合 LLM 决策的暂停模式。

**架构落地**:
```
LLM 输出单个 action(JSON,符合 copilot-schema)
  → AsstAppendTask("SingleStep", {type:"copilot", subtask:"action", details: action})
  → 执行 → AsstCallback 回传结果
  → (MaaAI CV + MAA 结构化 + 云 VLM) 读当前战局 → 喂 LLM
  → LLM 出下一个 action → 循环
```
**残余工作**:回调字段文档自陈「可能过时」,战局状态(场上干员位置/HP/技能就绪)的现成回调字段未在 `callback-schema.html` 明列——需读 MAA 源码 `src/MaaWpfGui/Main/AsstProxy.cs` 确认,或改用 MaaAI CV 主动读屏补状态。这属于 P1 实现细节,不构成命门。

**真实环境实证(2026-08-18)**:在真实 MuMu + 明日方舟 + MAA v6.16.8 跑通 SingleStep **实时决策闭环**——游戏进战斗后,循环「截图(MAA get_image)→ 云 VLM(通义千问-VL)看战况 → DeepSeek V4 即时决策 action → SingleStep `subtype=action` 喂入 → MAA 执行(`CopilotAction` 回调,如 SwitchSpeed)→ AllTasksCompleted → 循环」。**Phase 3 命门在真实战斗中最终实证**。

**integration 文档错误修正(源码 `SingleStepTask.cpp` / `CopilotConfig.cpp` 为准)**:
- SingleStep params 用 `subtype`(非文档的 `subtask`)
- `stage` 的 details 用 `stage_name`(非文档的 `stage`)
- `action` 的 details 需 `{"actions":[action]}`(非单个 action)
- `set_params` 返回 false 则 tid=0(append 失败)

**BattleStartAll 限制**:SingleStep 的 `subtype=start`(点开始作战)在编队准备界面报 20000(MAA 识别开始作战按钮失败,原因待查)。绕过:手动点开始作战进战斗,再 SingleStep action 循环;或用 Copilot 任务(`formation:true`)进战斗。

### 2. MaaAI——感知层可复用,无 LLM

- 仓库 `github.com/MaaAssistantArknights/MaaAI`(MIT,242★,2026-06 仍活跃),定位是「明日方舟深度学习模型,MAA 最佳实践」,**纯 CV 模型集合,无 LLM/Agent/对话能力**。
- 可复用模型(均 CPU ms 级、9–18M):
  - OCR(PaddleOCR finetune)— 读游戏文本
  - 技能 Ready 识别(MobileNetv4 三分类,9M,<1ms)
  - 干员血条检测(YOLOv8n,12M,~50ms)
  - 干员方向识别(MnistSimpleCNN 四分类,18M,~20ms)
- **作用**:正好补 MAA 标准 CV 读不到的细粒度战局状态,作为 LLM 决策前的 state extractor。C++ 推理参考在 MAA 主仓库 `src/MaaCore/Vision/Battle/BattlefieldClassifier.cpp` / `BattlefieldDetector.cpp`。
- **不能复用**:LLM 决策、对话、弹幕交互、语音、形象——均需自建。

### 3. B站 / 国家合规——两办法已施行

- **《人工智能生成合成内容标识办法》**(网信办/工信部/公安部/广电总局,2025-09-01 施行,**已生效**):**双重标识强制**——显式(用户可明显感知)+ 隐式(元数据/水印)。虚拟场景类要求**起始画面 + 持续过程**均加显著提示。文本/音频/图片/视频各有位置要求。已有执法案例(剪映/猫箱/即梦AI 被查处)。
- **《人工智能拟人化互动服务管理暂行办法》**(2026-07-15 施行,**已生效**):**严禁向未成年人提供虚拟伴侣**等服务。AI 虚拟主播是否归类「拟人化互动」及备案口径——**办法全文需人工核实**(词条 403)。
- **生成式 AI 服务备案**:须在显著位置公示**模型名称 + 备案编号**,并加标识。DeepSeek 作为大模型应已备案,**需查其公开备案号并公示**。用未备案 AI 服务属违规。
- **实时生成难点**:无法事前审核、标识需实时嵌入、LLM 幻觉不可控、深度合成冒充真人是重点打击。
- **红线**:未标识/篡改标识、深伪冒充真人、向未成年人提供虚拟伴侣、用未备案 AI、涉政暴恐色情侮辱。
- **工程兜底**:敏感词过滤 + 延时直播 + 兜底话术 + 人工巡查 + **日志留存≥6 个月**(标识办法第九条)。
- **B站平台细则原文待人工核实**(AI 创作分区 `bilibili.com/v/ai/`、客服页未抓到正文)。

### 4. 三路云调用降级框架设计

**策略**:每路云调用统一封装 `超时 + 重试 + 降级 fallback + 熔断`。

| 路 | 主路径 | 超时 | 重试 | 降级 fallback | 熔断阈值 | 冷却 |
|---|---|---|---|---|---|---|
| LLM | DeepSeek chat | 8s | 2 | 规则脚本/固定话术(游戏)或安全话术(聊天) | 3 次连败 | 60s |
| TTS | 云端音色 TTS | 5s | 2 | edge-tts;再挂→静默 + OBS 字幕 | 3 | 60s |
| VLM | 云端 VLM | 12s | 1 | 纯 MAA 结构化 + MaaAI CV 特征喂 LLM(不走 VLM) | 2 | 90s |

**熔断状态机**:`OK →(连续失败达阈值)→ DOWN(冷却期内直接 fallback)→(冷却到期)→ DEGRADED(半开,试探一次)→ 成功则 OK / 失败则 DOWN`。

**伪代码骨架**(asyncio):
```python
import asyncio, time
from dataclasses import dataclass
from enum import Enum

class Level(Enum):
    OK = 0; DEGRADED = 1; DOWN = 2

@dataclass
class Circuit:
    fails: int = 0
    opened_at: float | None = None
    level: Level = Level.OK

class GuardedCall:
    """三路云调用统一封装:超时+重试+降级+熔断。fallback 必须永不抛错。"""
    def __init__(self, name, primary, fallback, *, timeout=8.0, retries=2,
                 fail_threshold=3, cool=60.0):
        self.name, self.primary, self.fallback = name, primary, fallback
        self.timeout, self.retries = timeout, retries
        self.fail_threshold, self.cool = fail_threshold, cool
        self.cb = Circuit()

    async def __call__(self, *a, **k):
        # 熔断冷却期内:直接 fallback
        if self.cb.level == Level.DOWN and self.cb.opened_at \
           and time.time() - self.cb.opened_at < self.cool:
            return await self.fallback(*a, **k)
        # 冷却到期:半开放行一次试探
        if self.cb.level == Level.DOWN:
            self.cb.level = Level.DEGRADED
        # 主路径重试
        for attempt in range(self.retries + 1):
            try:
                r = await asyncio.wait_for(self.primary(*a, **k), self.timeout)
                self._on_ok()
                return r
            except Exception:
                if attempt < self.retries:
                    await asyncio.sleep(0.3 * (attempt + 1)); continue
        # 主路径全失败:走 fallback + 计入熔断
        self._on_fail()
        return await self.fallback(*a, **k)

    def _on_ok(self):
        self.cb = Circuit(); self.cb.level = Level.OK
    def _on_fail(self):
        self.cb.fails += 1
        if self.cb.fails >= self.fail_threshold:
            self.cb.level = Level.DOWN; self.cb.opened_at = time.time()
        else:
            self.cb.level = Level.DEGRADED

# 三路实例化(伪代码)
llm = GuardedCall("llm", primary=deepseek_chat, fallback=rule_or_safe_line,
                  timeout=8.0, retries=2, fail_threshold=3, cool=60.0)
tts = GuardedCall("tts", primary=cloud_voice_tts, fallback=edge_tts_then_subtitle,
                  timeout=5.0, retries=2, fail_threshold=3, cool=60.0)
vlm = GuardedCall("vlm", primary=cloud_vlm_describe, fallback=maa_struct_only,
                  timeout=12.0, retries=1, fail_threshold=2, cool=90.0)
```

**要点**:
- `fallback` 必须永不抛错(它本身是本地/规则逻辑,不依赖云)。
- VLM 熔断阈值更低(2 次)、冷却更长(90s),因决策可暂停,容得起等;且 VLM 失败后退到「纯结构化特征」仍能决策,只是理解弱。
- 聊天路 LLM 熔断时用「安全话术」而非静默,避免冷场;游戏路 LLM 熔断退规则脚本。
- 所有调用记日志(成本统计 + 合规留存≥6 个月)。

---

## 八、总评

方案方向正确、选型成熟、Phase 切分合理,作者对 Neuro-sama 复刻关键认知(人设×互动记忆×真能玩×持续运营)抓得准。

**本轮 P0 调研后结论修正**:
- 原「Phase 3 命门(MaaPy 动态喂入)未验证」→ **已解除**:MAA 原生 `SingleStep` 单步喂 action + 回调闭环完全满足「LLM 实时决策」需求,Phase 3 可行性从「中高」升为「高」。
- **MaaAI 提供现成感知层 CV 模型**(血条/技能/朝向/OCR),可直接复用为 LLM 决策前的 state extractor,省去自训感知模型。
- **合规风险已具体化**:两办法已生效,须前置做标识(显式+水印)、备案号公示、避开虚拟伴侣/未成年人叙事、日志留存 6 个月。
- **云 API 化**后本地算力不再是瓶颈,门槛降低;代价是成本/可用性/延迟/隐私四类风险,用第八节降级框架 + 「简单走规则、难局才上云」对冲。

**剩余主要短板**:Phase 2 本地微调与第七节自评矛盾(建议弱化);编排层调度/抢占、感知三路融合、合规落地等设计缺口待 Phase 1 补齐。建议下一步按 P1 推进:`SingleStep` 最小 demo + 编排调度设计 + 感知融合数据结构。
