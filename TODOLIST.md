# AI 明日方舟主播 - 待办与规划

## 当前状态

- **L1 已验证**: 1-7 通关成功 (DeepSeek 生成作业 + MAA Copilot 一体化执行)
- **架构**: MAA Copilot 开环模式 (DeepSeek 生成作业 → 后处理 → MAA 盲执行)
- **核心瓶颈**: DeepSeek 缺乏游戏知识 (出怪时序/干员技能/敌人属性)

---

## L2: 知识库建设 (预计 3.5 天)

### Phase 1: 数据下载 (0.5 天)

- [ ] 下载 ArknightsGameData (GitHub: Kengxxiao/ArknightsGameData)
  - 稀疏检出 `zh_CN/gamedata/` 子目录
  - 存放到 `data/gamedata/`
  - 核心文件:
    - `levels/obt/main/level_main_01-07.json` (出怪波次)
    - `levels/enemydata/enemy_database.json` (14.7MB, 敌人属性)
    - `excel/character_table.json` (干员技能数据)
    - `excel/skill_table.json` (技能描述)
    - `excel/enemy_handbook_table.json` (敌人中文名)

### Phase 2: 解析器开发 (1.5 天)

- [ ] `src/data/wave_parser.py` — 出怪波次解析器
  - 递归展开 waves → fragments → actions
  - 累积 preDelay 得绝对出怪时间
  - routeIndex → routes[idx] → 路线描述 (上/中/下路, 起点→终点)
  - enemy key → enemy_handbook → 中文名
  - 输出紧凑文本: `"T+2s: 源石虫×1 上路 | T+5s: 源石虫×3 中路 间隔1s"`

- [ ] `src/data/skill_extractor.py` — 干员技能提取器
  - MAA char_id → character_table.json 匹配
  - 提取技能名/描述/效果/持续时间/CD
  - 输出: `"桃金娘: 技能1'支援号·β' 回复6费 CD25s"`

- [ ] `src/data/enemy_lookup.py` — 敌人属性查询
  - enemy_id → enemy_database.json → HP/ATK/DEF/RES/moveSpeed
  - 输出: `"源石虫: HP1030 ATK42 | 士兵: HP1000 ATK130 DEF50"`

### Phase 3: 集成 (0.5 天)

- [ ] 更新 `src/data/map_info.py`
  - MapInfo 加 `waves` 字段
  - `to_description()` 包含波次时间线 + 敌人属性摘要

- [ ] 更新 `src/brain/llm_client.py`
  - SYSTEM_PROMPT_COPILOT 加入:
    - 出怪波次时序 (让 DeepSeek 按出怪顺序部署)
    - 干员技能描述 (改善干员选择和技能使用)
    - 敌人属性 (改善干员搭配)
    - Retreat + 二次部署机制
    - `kills` 字段用法 (在击杀到 X 时部署)
  - 加 `temperature=0` 保证作业一致性

- [ ] 更新 `src/real_run.py`
  - 调用 wave_parser 解析 level JSON
  - 调用 skill_extractor 提取选中干员技能
  - 调用 enemy_lookup 查询敌人属性
  - 喂完整上下文给 DeepSeek

### Phase 4: 验证 (0.5 天)

- [ ] 1-7 跑 3 次 `--llm`
  - 检查: 部署顺序匹配出怪顺序
  - 检查: 三路覆盖 (上路/中路/下路都有干员)
  - 检查: 无漏怪
- [ ] 通关作业存到 `job_cache/main_01-07.json`

### Phase 5: 胜负检测 (L3 预览, 0.5 天)

- [ ] MAA `AllTasksCompleted` 后截图
  - 用 `StageDrops-Stars-2/3` 模板检测胜负
  - 胜: 缓存作业
  - 负: 标记失败, 可重试

---

## L3: 多关卡 + 失败重试 (预计 +2-3 天)

- [ ] 作业缓存机制
  - 通关后保存到 `job_cache/<stage_id>.json`
  - 下次直接用缓存, 不调 LLM
- [ ] 胜负检测集成
  - MAA 完成后截图检测 Stars
  - 失败自动重试 (改 prompt 或换干员)
- [ ] 多关卡支持
  - 4177 个 tile 文件覆盖所有关卡
  - 每关下载对应 level JSON
  - 关卡选择 UI 自动化 (MAA tasks/Stages 模板)

---

## L4: 完整 AI 主播 (预计 +1-2 周)

### 语音
- [ ] 云端 TTS API (阿里云/腾讯云/Azure)
- [ ] 文本 → 语音流, 控制语速/情感

### 虚拟形象
- [ ] VTube Studio WebSocket API
- [ ] LLM 输出 → 表情/动作触发

### 推流
- [ ] OBS WebSocket API
- [ ] 场景切换 (游戏画面/虚拟形象/弹幕)

### 弹幕互动
- [ ] B站直播弹幕 API (WebSocket)
- [ ] 弹幕 → 调度器 → LLM 回复
- [ ] @主播 优先处理

### 主播人格
- [ ] 人格 prompt (性格/说话风格/口头禅)
- [ ] 聊天记忆 (RAG)
- [ ] 思考旁白 (决策时播报)

---

## 架构决策记录

### MAA Copilot 开环模式 (当前)
- DeepSeek 生成整关作业 → MAA 盲执行
- 优点: 可靠 (MAA deploy_oper 闭环)
- 缺点: 无法战斗中适应变化

### SingleStep 实时模式 (已放弃)
- MAA SingleStep `_run` 缺 `update_deployment`
- 每次 deploy 后不重新识别待部署区 → 干员列表过期
- TODO: 调查能否修改 MAA 源码补上 update_deployment

### ADB 实时控制 (已放弃)
- CV 感知不可靠 (flag 重复/识别不准)
- tile_calc 坐标正确但拖拽精度不够
- MAA deploy_oper 比我们 ADB 方式可靠得多

---

## 数据源参考

| 数据源 | 用途 | 位置 |
|--------|------|------|
| MAA 安装 | 执行/感知/模板 | `C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\` |
| MAA 源码 | 参考 | `C:\demo\MaaAssistantArknights-dev-v2\` |
| ArknightsGameData | 游戏数据 | `data/gamedata/` (待下载) |
| prts.wiki | 补充数据 | `https://prts.wiki/api.php` |
| MuMu 模拟器 | 游戏运行 | `127.0.0.1:16384` |
| DeepSeek API | LLM 大脑 | `.env` DEEPSEEK_API_KEY |
| 通义千问-VL | VLM 感知 | `.env` VLM_API_KEY |
