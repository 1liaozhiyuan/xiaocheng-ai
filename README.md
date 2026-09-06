# 陪伴 Agent（多 Agent + 长期记忆）

一个基于多 Agent 架构与分层长期记忆的个性化聊天陪伴 Agent。当前为 **V5**：
V0-V4（对话、记忆、画像、主动性、Web 界面）之上，新增长期情绪趋势追踪、
记忆规模扩展与会话恢复。

## 架构

```
启动 ──► 主动开场 Agent（时段+约定+特殊日子+危机跟进 → 问候先声）
用户消息 ──► 策略 Agent（情绪/意图/危机分级 low|high，轻模型亚秒级）
                │ 基调指令（分级守护模式）
                ▼
        主对话 Agent（人设+画像+上次摘要+分层记忆+近期历史+策略）──► 流式回复
                    ▲
                    │ 分层注入：当前记忆 + 历史记忆（【过往】标注）
              ┌─────┴─────┐
              │ 记忆检索层  │  向量相似度 + 重要性兜底 + 时间衰减
              └─────▲─────┘
                    │ 异步（不阻塞聊天）
        记忆管理 Agent：抽取 → 主题粗筛(向量+LLM) → 合并决策
                        (ADD/SUPERSEDE/MERGE/SKIP) → 入库 → 遗忘整理

退出时「睡前整理」：会话复盘（补漏抽取+同义去重+摘要）→ 画像巩固
下次启动：主动开场优先跟进危机状态/约定/特殊日子，关系跨会话延续
```

### 记忆取代链（V1 核心）

新记忆与旧记忆冲突时不覆盖：新记忆入库为 active，旧记忆转为 `superseded`
并链接到新记忆（`superseded_by`）。日常聊天只注入当前状态；用户问历史时
（「我住过哪些城市」）历史记忆被语义命中、以【过往】标注注入——现状与履历两不误。

### 画像与策略（V2 核心）

- **画像**：`user_profile` KV 存当前状态快照（称呼/职业/宠物/近期状态/说话风格…），
  会话结束时从记忆归纳；记忆层保留完整轨迹，画像回答「现在」，记忆回答「曾经」
- **策略**：每轮先由轻量模型判断情绪/意图，产出一句基调指令。策略是增强件：
  任何失败自动回退，绝不阻塞聊天

### 主动性与分级守护（V3 核心）

- **主动开场**：启动时小澄先说话（流式打字效果），按优先级融合：危机跟进 >
  特殊日子祝福 > 约定跟进 > 日常轻问候。`PROACTIVE_GREETING=false` 可关闭
- **危机分级**：low（无望感）→ 认真共情+建议专业支持；high（明确自伤意念）→
  直接提供心理援助热线 12356 与紧急求助指引。危机信号写入「关注标记」，
  下次启动的开场白会主动关心（宁可误报守护，不可漏报）

### 情绪趋势与体验完善（V5 核心）

- **长期情绪趋势**：每次睡前整理生成情绪快照（1~10 分 + 标签 + 备注），
  画像面板绘制「最近心情走势」条形图；趋势连续低迷（均分 < 4）时，
  主动开场会自动变得更关切
- **会话恢复**：刷新页面自动重放对话流（最近 100 条），不再丢上下文
- **记忆规模扩展**：联合粗筛的记忆池超过 40 条时先做向量预筛，
  低重要性但相关的记忆不再被遗漏

```
当前记忆:  用户下周就搬去上海了，不再住北京   ← active，日常注入
历史记忆:  用户这些年一直住在北京            ← superseded，问历史时召回
```

## 快速开始

项目为**前后端分离**架构：`server/`（FastAPI 纯 API 服务）+ `frontend/`（Vue 3 + Vite 工程）。

```bash
pip install -r requirements.txt
copy .env.example .env      # 填入 ZHIPU_API_KEY（https://open.bigmodel.cn 免费注册）
python main.py --check      # 自检：依赖 / key / 存储 / LLM 连通

# ── 方式一：生产模式（推荐日常使用，单服务）──
cd frontend && npm install && npm run build && cd ..
python server/app.py        # 自动托管 frontend/dist，浏览器打开 http://127.0.0.1:8001

# ── 方式二：前后端分离开发模式（改前端热更新）──
python server/app.py                 # 后端 API 在 8001
cd frontend && npm run dev           # Vite 5173，/api 自动代理到 8001
                                     # 浏览器打开 http://localhost:5173

# ── 方式三：CLI 终端聊天 ──
python main.py
```

- Web 界面：左侧聊天（流式打字效果），右侧面板实时查看「画像（含心情走势图）/ 当前记忆 /
  过往经历 / 归档」，每条当前记忆可一键遗忘；「🌙 小憩一下」手动触发睡前整理；
  会话闲置 30 分钟自动整理；刷新页面自动恢复对话流
- CLI 聊天中 `/memory` 看记忆、`/profile` 看画像、`/forget N` 遗忘、`/exit` 退出（自动整理）
- 验证脚本：`scripts/v5_check.py`、`v4_check.py`、`v3_system_test.py`（危机矩阵等系统测试）
- 单测：`python -m unittest discover tests -v`（51 个，API 层离线可跑）

> 提示：本机若开着代理软件（Clash 等）的全局/TUN 模式，请把 `127.0.0.1` 加入代理
> 排除列表，否则浏览器可能无法访问本地服务（验证脚本已用 `trust_env=False` 规避）。

## 模型与成本

| 用途 | 默认模型 | 服务商 |
|---|---|---|
| 对话 | glm-4.5-flash | 智谱（免费，已关闭思考模式保证响应速度） |
| 记忆抽取 | glm-4-flash | 智谱（免费、快，结构化 JSON 输出） |
| 向量化 | qwen3.7-text-embedding-flash | 阿里百炼（`QWEN_API_KEY`，独立于对话厂商） |

向量化不可用（key 缺失/余额不足）时自动降级为「重要性排序」注入，恢复后重启自动启用。

## 目录结构

```
config.py               全局配置（模型、记忆参数、人设开关）
main.py                 CLI 入口 + 自检
llm/client.py           LLM 客户端：强模型对话 / 轻量模型抽取，JSON 容错解析
persona/prompts.py      所有 prompt（人设、记忆抽取规则）
memory/
  store.py              SQLite：messages / memories / user_profile / sessions
  vector_store.py       chromadb 封装（千问 qwen3.7 embedding，OpenAI 兼容）
  retriever.py          分层检索：当前记忆 + 历史记忆（语义命中/历史关键词）
  extractor.py          抽取 → 主题粗筛（向量快筛 + LLM 兜底）→ 合并决策 → 入库
  curator.py            遗忘衰减：按类别半衰期，低有效性转归档（不删除）
  reviewer.py           会话复盘：补漏抽取（复用抽取管线）+ 同义去重 + 摘要
agents/
  companion.py          主对话 Agent（Persona）
  memory_manager.py     记忆管理 Agent（后台异步调度）
  strategist.py         策略 Agent：情绪/意图判断 + 危机分级守护指令
  profile_agent.py      画像 Agent：记忆 → 结构化画像（睡前整理）
  proactive.py          主动开场 Agent：危机跟进/特殊日子/约定/日常问候
server/
  state.py              进程级组件装配 + Web 会话状态
  api.py                路由：SSE 流式聊天/开场、记忆面板、复盘、遗忘
  app.py                FastAPI 应用工厂 + CORS + 闲置自动复盘定时器
frontend/               Vue 3 + Vite 前端工程（前后端分离）
  src/App.vue           状态中枢：会话生命周期、SSE 处理、面板数据
  src/components/       ChatPanel / SidePanel / ProfileTab / MemoryTab
  src/api/client.js     API 客户端（fetch + SSE 解析）
  vite.config.js        dev 代理（/api → 8001）；build 产出 dist 由后端托管
tests/                  存储层单测 + API 单测 + 全链路冒烟测试（离线可跑）
data/                   运行时数据（gitignore）：chat.db + chroma/
```

## 路线图

| 阶段 | 内容 | 主要落点 |
|---|---|---|
| V0 ✅ | 人设对话 + 最小记忆闭环（含 embedding 降级） | 已完成 |
| V1 ✅ | 记忆取代链（SUPERSEDE 决策 + 分层注入）、遗忘衰减 | `memory/extractor.py`、`memory/curator.py` |
| V2 ✅ | 会话复盘+去重+摘要、画像巩固、情绪策略 + 危机守护 | `memory/reviewer.py`、`agents/profile_agent.py`、`agents/strategist.py` |
| V3 ✅ | 主动开场（危机跟进/特殊日子/约定）、危机分级守护 | `agents/proactive.py`、`agents/strategist.py` |
| V4 ✅ | Web 界面（FastAPI + Vue3）：SSE 流式聊天 + 记忆/画像可视化面板 | `server/`、`frontend/` |
| V5 ✅ | 长期情绪趋势（快照/可视化/开场联动）、会话恢复、粗筛向量预筛 | `memory/reviewer.py`、`server/api.py`、`frontend/` |
| V6 ✅ | 前后端分离工程化（Vue SFC + Vite，生产单服务托管） | `frontend/`、`server/app.py` |
| V7 | 微信/QQ 接入（复用 API 层做适配器）、主动性的时机优化 | 新增适配器层 |

## 已知限制（V4 方向）

- 系统测试（`scripts/system_test.py`）发现的模型波动问题：同义记忆偶尔并存、
  编造细节偶发——靠 prompt 硬约束 + 代码兜底 + 复盘去重压制，无法根除
- 粗筛只看 importance 前 40 条记忆，记忆量大后需改为向量预筛
- 主动开场每次启动生成一次，夜间/白天风格依赖模型对时段的把握，偶有平淡

## 关键设计

- **双模型降成本**：对话用 `CHAT_MODEL`（可换 glm-4.5），抽取/画像用 `LITE_MODEL`（glm-4-flash 免费）
- **SQLite 为 source of truth**：向量库只是检索索引，记忆增删改以 SQLite 为准
- **主链路不等待**：记忆抽取 fire-and-forget，失败只警告、不影响对话
- **换厂商**：所有调用走 OpenAI 兼容协议，改 `.env` 的 `LLM_BASE_URL` 即可切 DeepSeek/Qwen 等
