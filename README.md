# AI Film Agent

> 本地优先（Local-first）的多智能体 AI 短片生产工作流系统
> ModelScope「AI+∞ 开发者创作大赛」参赛作品

这不是一个 "Prompt → Video" 的一步出片工具。

它把一套完整的 AI 影视生产方法，固化为**可持续运行、可暂停、可修改、可回溯、可重新生成**的多 Agent 工作流：真实影视工业中的策划、编剧、导演、分镜、Dailies 审核、选片与剪辑，被重新映射为 Agent 规划、资产锁定、Storyboard、生成、审核、选择、Timeline 与渲染。人在任何关键创作节点都可以介入。

**本文档同时是交接文档**：接口清单、配置项、目录职责、已验证范围、已知坑与未完成项全部写在下面，接手者读完即可继续开发。设计依据见 [项目概述.md](项目概述.md)（规格书 v1.0，下文引用「§N」均指该文件章节）。

---

## 目录

- [当前状态速览](#当前状态速览)
- [核心差异点：Take 抽卡](#核心差异点take-抽卡)
- [工作流总览](#工作流总览)
- [快速开始](#快速开始)
- [配置项全清单](#配置项全清单)
- [HTTP 接口全清单](#http-接口全清单)
- [代码结构与模块职责](#代码结构与模块职责)
- [数据模型](#数据模型)
- [统一生成队列](#统一生成队列)
- [Agent 体系](#agent-体系)
- [Model Agnostic 与生成后端](#model-agnostic-与生成后端)
- [合规节点](#合规节点)
- [项目存储布局](#项目存储布局)
- [已知问题与踩过的坑](#已知问题与踩过的坑)
- [未完成部分（按优先级）](#未完成部分按优先级)
- [验收与自测方法](#验收与自测方法)
- [路线图](#路线图)

---

## 当前状态速览

| 模块 | 状态 | 说明 |
|---|---|---|
| 底层生产链（Shot→Take→Select→Timeline→Preview→Render） | ✅ 已验收 | Mock 全链路 E2E 通过，FFmpeg 成片可播 |
| Storyboard 候选 / 选定 / 锁定 | ✅ 已验收 | 前后端均已接通 |
| 统一生成队列 + Mock / MiniMax / ModelScope Adapter | ✅ 代码完成 | Mock 已验收；云后端**未用真实 Key 联调** |
| 合规节点（Prompt 闸门 + 审计日志） | ✅ 已验收 | 三个入口全部接入 |
| LangGraph 主流程 + 6 个 Agent（一句话全自动） | ✅ 已验收 | Mock 下 18 秒成片，含自动重拍 |
| Preview 多片段连续播放 | ✅ 已验收 | 浏览器实测 6/6 片段、seek、重播无报错 |
| 角色 / 场景资产链（Character / Location） | 🟡 后端完成，前端未做 | 建卡→三视图→锁定→Shot 自动引用→锚点注入提示词，后端 E2E 跑通一次；**前端资产面板未实现** |
| VideoUnderstandingModel（Reviewer 真实看片） | ❌ 未实现 | 当前 Reviewer 只看文本，评分是模板值 |
| 实时事件推送（SSE / WebSocket） | ❌ 未实现 | 前端靠 5s 轮询 |
| Director Mode（interrupt / resume 人工介入） | ❌ 未实现 | 图目前一次性跑完，不可中途介入 |
| 快照 / 版本 diff | ❌ 未实现 | AgentArtifact 已是版本化底座，缺 diff API |
| Editor 转场 / 字幕 / BGM | ❌ 未实现 | 现在只是顺序拼接 |
| 自动化测试（pytest） | ❌ 未实现 | 仓库内无 tests 目录 |
| 微调 / 量化、参赛短片、复现 Notebook | ❌ 未开始 | 比赛评分项，见[未完成部分](#未完成部分按优先级) |

最新提交：`9436087`（Preview 连续播放）之后的工作树包含资产链后端与队列并发修复，见下文。

---

## 核心差异点：Take 抽卡

本系统最重要的产品与数据设计是：

> 把生成结果作为**可管理、可审核、可比较、可选择、可重新生成**的 Take。

```text
Project → Scene → Shot → Storyboard → Take[] → selected_take → Timeline
                    ↑
        Character / Location 资产（锁定后自动被所有 Shot 引用）
```

围绕这条数据链，系统提供：

- 每个 Shot 生成任意数量的 Take，进入 Take Pool 并排比较
- Reviewer Agent 对每个 Take 多维打分：Prompt 遵循度、角色一致性、时序一致性、运动质量、构图、叙事意图、连贯性
- 有限自动重抽（默认最多 2 轮），超出即按最高分兜底并记录 `blocked_reason` 供人工复核（规格要求此处 interrupt 交还用户，**尚未实现**）
- 用户随时切换 selected_take，Timeline 引用与实时 Preview 立即生效
- 每个 Take 的 Prompt / 模型 / Seed / 参数全程留痕，支持版本 diff 与追溯

---

## 工作流总览

```text
Idea / 人机讨论
        ↓
剧本（Writer）
        ↓
角色 / 场景资产（Card + 三视图 / 参考图，锁定）
        ↓
导演方案（Director Bible）+ 生产计划（Producer）
        ↓
Storyboard（多候选 → 选择 → 锁定）
        ↓
Shot → 多 Take 生成
        ↓
AI Review（Reviewer，AI Dailies）
        ↓
用户 / Agent 选片
        ↓
Editor 自动剪辑 → Timeline
        ↓
实时 Preview（基于 Proxy，不重渲染）
        ↓
FFmpeg Final Render（使用原始素材）
```

LangGraph 节点与状态机（`backend/app/graph/film_graph.py`）：

```text
START → ideation → scripting → asset_design → storyboarding
      → video_generation → reviewing ─┬─(有镜头无 KEEP 且未超重拍上限)→ video_generation
                                      └─→ take_selection → editing → rendering → complete → END
```

### 两种工作模式

- **Auto Mode**：一句话创意进入，Agent 最大程度自主完成全流程；仅在生成连续失败、超出自动重抽上限、必要资产缺失、严重模型异常时强制 interrupt。当前实现：`POST /one-sentence` 只接受 `mode=AUTO` 的项目，资产在该模式下自动确认锁定。
- **Director Mode**：用户可在任意阶段介入——修改剧本 / Shot / Prompt、重抽 Storyboard、锁定分镜、设置 Take 数量、选择 Take、修改 Timeline、覆盖 Editor 决策。当前实现：`mode=DIRECTOR` 的项目可走全部单项接口（建 Scene/Shot、生成分镜、生成 Take、选片、剪辑、渲染），但**图内 interrupt/resume 尚未实现**，`asset_design` 节点在非 AUTO 模式下不会自动锁定资产（会停在 `PENDING_CONFIRM` 等人工调 `/lock`）。

---

## 快速开始

依赖：Python 3.11+（开发实测 3.14.6）、Node 18+（实测 v24）、**FFmpeg 9.x**（`brew install ffmpeg`，必须可用，Proxy 与成片全靠它）。

```bash
# 后端（端口 8765，前端默认指向它）
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8765

# 前端（端口 5173）
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`，在「一句话生成短片」输入创意并点击启动，即可全自动完成：剧本 → 导演圣经 → 角色/场景资产 → 分镜候选 → Take 生成 → 审核（自动重拍）→ 选片 → 剪辑 → 成片渲染。右侧「一句话全自动流程」面板实时显示各阶段进度。

默认使用 Mock 文本模型与 Mock 生成后端（**无需任何 API Key / GPU**）。切换真实后端见[配置项全清单](#配置项全清单)。

后端 API 文档：`http://127.0.0.1:8765/docs`（Swagger，随代码自动生成，是最权威的接口清单）。

---

## 配置项全清单

全部通过环境变量配置，前缀 `FILMAGENT_`，定义在 `backend/app/config.py`（pydantic-settings，`extra=forbid`，写错变量名会直接启动失败）。也可以放到启动 shell 的 env 里；仓库不读 `.env` 文件（`env_file=None`），需要的话自行 `export` 或在 `Settings.model_config` 加 `env_file`。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FILMAGENT_DATA_DIR` | `<repo>/data` | 所有项目数据根目录（已被 .gitignore 忽略） |
| `FILMAGENT_PROXY_HEIGHT` | `720` | Proxy 转码高度 |
| `FILMAGENT_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | 额外允许的跨域来源；`localhost/127.0.0.1` 任意端口已由正则放行 |
| `FILMAGENT_IMAGE_BACKEND` | `mock` | `mock` \| `modelscope` |
| `FILMAGENT_VIDEO_BACKEND` | `mock` | `mock` \| `minimax` \| `modelscope` |
| `FILMAGENT_VIDEO_GENERATION_CONCURRENCY` | `1` | VIDEO 任务并发信号量（§24）。云后端请按配额调整 |
| `FILMAGENT_MAX_AUTO_RETAKE_ROUNDS` | `2` | 单镜头自动重拍上限（§12） |
| `FILMAGENT_LLM_BACKEND` | `mock` | `mock` \| `openai`（任意 OpenAI 兼容端点） |
| `FILMAGENT_LLM_API_KEY` | 空 | 文本模型 Key |
| `FILMAGENT_LLM_BASE_URL` | `https://api-inference.modelscope.cn` | 文本模型端点 |
| `FILMAGENT_LLM_MODEL` | `Qwen/Qwen3.5-35B-A3B` | 文本模型名 |
| `FILMAGENT_MINIMAX_API_KEY` | 空 | MiniMax Key（视频） |
| `FILMAGENT_MINIMAX_BASE_URL` | `https://api.minimax.cn` | |
| `FILMAGENT_MINIMAX_VIDEO_MODEL` | `MiniMax-H3` | |
| `FILMAGENT_MODELSCOPE_API_KEY` | 空 | ModelScope API-Inference Key（图像/视频） |
| `FILMAGENT_MODELSCOPE_BASE_URL` | `https://api-inference.modelscope.cn` | |
| `FILMAGENT_MODELSCOPE_IMAGE_MODEL` | `Qwen/Qwen-Image` | |
| `FILMAGENT_MODELSCOPE_VIDEO_MODEL` | `Wan-AI/Wan2.2-T2V-Fast` | |

前端：`VITE_API_BASE`（默认 `http://127.0.0.1:8765`），见 `frontend/src/api/client.ts`。

Key 缺失时对应云后端不可用（会抛错并把 job 标 FAILED），Mock 后端始终可用。

---

## HTTP 接口全清单

前缀 `/api/v1`；除 `health` 与 `projects` 列表/创建外，全部带 `{project_id}` 路径参数，会话依赖 `project_session` 自动打开该项目的 SQLite。媒体文件走静态挂载 `GET /media/{project_id}/{相对路径}`。

### 健康检查 / 项目（`api/projects.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 存活探针 |
| GET | `/projects` | 项目列表 |
| POST | `/projects` | 创建项目（`name`、`idea`、`mode=AUTO\|DIRECTOR`、`default_take_count`）；同时建目录与 project.db |
| GET | `/projects/{pid}` | 项目详情 |
| DELETE | `/projects/{pid}` | 删除项目（含磁盘目录） |

### 场景 / 镜头（`api/scenes.py`、`api/shots.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/projects/{pid}/scenes` | 场景列表 / 新建 |
| GET/POST | `/projects/{pid}/shots` | 镜头列表 / 新建 |
| PUT | `/projects/{pid}/shots/{shot_id}` | 修改镜头（描述、景别、运镜、时长、take_count 等） |
| DELETE | `/projects/{pid}/shots/{shot_id}` | 删除镜头 |
| POST | `/projects/{pid}/shots/{shot_id}/takes/{take_id}/select` | 选片：设置 `selected_take_id` |

### 角色 / 场景资产（`api/assets.py`）— 新增，前端尚未接

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects/{pid}/assets` | 列出全部角色与场景资产，含 `references[]`（`view` / `source` / `media_url` / `meta.seed`）、`prompt_block`、`prompt_block_hash`、`seed`、`status`、可选 `views` |
| POST | `/projects/{pid}/assets/link-shots` | 重跑「锁定后所有 Shot 自动引用」（§14），返回 `{shot_id: [character_id]}` |
| POST | `/projects/{pid}/characters` | 手工建角色卡（`source=IMPORT` 时对应用户自填入口） |
| PATCH | `/projects/{pid}/characters/{cid}` | 改角色卡；**已 LOCKED 返回 409** |
| POST | `/projects/{pid}/characters/{cid}/references/generate` | 入队生成三视图 FRONT/SIDE/BACK（768×1024，`job_type=CHARACTER`），202 返回 job |
| POST | `/projects/{pid}/characters/{cid}/references` | 上传参考图（multipart，`view` 表单字段，`source=USER`） |
| POST | `/projects/{pid}/characters/{cid}/lock` | 锁定：编译 `prompt_block` + `hash`、固化 `seed`、`version+1`，随后自动重跑 Shot 引用 |
| POST | `/projects/{pid}/locations` | 手工建场景资产（可带 `scene_id`） |
| PATCH | `/projects/{pid}/locations/{lid}` | 改场景资产；LOCKED 返回 409 |
| POST | `/projects/{pid}/locations/{lid}/references/generate` | 入队生成 ESTABLISHING / KEY_ANGLE_A / KEY_ANGLE_B / DETAIL（`job_type=LOCATION`） |
| POST | `/projects/{pid}/locations/{lid}/references` | 上传场景参考图 |
| POST | `/projects/{pid}/locations/{lid}/lock` | 锁定场景资产 + 重跑 Shot 引用 |

资产状态机：`DRAFT`（建卡）→ `PENDING_CONFIRM`（出图完成，等用户确认）→ `LOCKED`（身份冻结）。AUTO 模式下 `asset_design` 节点自动完成确认。

### 分镜（`api/storyboards.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/projects/{pid}/shots/{shot_id}/storyboards/generate` | 入队生成候选（`prompt`/`count`/`width`/`height`），过合规闸门，202 返回 job |
| GET | `/projects/{pid}/shots/{shot_id}/storyboards` | 候选列表（含 `media_url`、`is_selected`、`is_locked`） |
| POST | `/projects/{pid}/storyboards/{sb_id}/select` | 选定候选（已锁定的不可改选，409） |
| POST | `/projects/{pid}/storyboards/{sb_id}/lock` | 锁定分镜（必须先 select，否则 409） |

### Take（`api/takes.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects/{pid}/shots/{shot_id}/takes` | Take 列表（含 proxy/original `media_url`） |
| POST | `/projects/{pid}/shots/{shot_id}/takes` | 上传已有视频作为 Take（multipart：`file`、`prompt`、`model_name`、`seed`）；自动生成 Proxy |
| POST | `/projects/{pid}/shots/{shot_id}/takes/generate` | 入队生成 N 个 Take（`job_type=VIDEO`），过合规闸门 |

### 时间线 / 渲染（`api/timeline.py`、`api/renders.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects/{pid}/timeline` | 时间线片段 |
| PUT | `/projects/{pid}/timeline` | 覆盖时间线（顺序、in/out、音量） |
| POST | `/projects/{pid}/timeline/auto-edit` | Editor 自动剪辑（按 selected_take 顺序组接） |
| POST | `/projects/{pid}/render` | FFmpeg 成片渲染，返回 `renders/` 下的相对路径 |

### 队列 / 流程 / 产物（`api/jobs.py`、`api/pipeline.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects/{pid}/generation-jobs` | 任务列表（可按 shot 过滤），前端轮询用 |
| POST | `/projects/{pid}/generation-jobs/{job_id}/cancel` | 取消任务（含调用 Adapter 的 cancel） |
| POST | `/projects/{pid}/one-sentence` | 启动一句话全自动流程（仅 `mode=AUTO`；已有进行中的 PIPELINE 返回 409），202 |
| GET | `/projects/{pid}/pipeline` | 当前 PIPELINE 任务 + 全部阶段记录（`stages[]`：`stage`/`note`） |
| GET | `/projects/{pid}/artifacts` | Agent 产物列表，可按 `kind` 过滤、`limit` 限制 |

`artifacts` 的 `kind` 取值：`screenplay`、`director_bible`、`production_plan`、`character_cards`、`location_cards`、`asset_lock`、`take_review`、`editing`、`stage`、`run_summary`。

---

## 代码结构与模块职责

```text
backend/app/
├── main.py              FastAPI 装配：CORS、api_router、/media 静态挂载、lifespan 启停生成队列
├── config.py            全部配置项（FILMAGENT_* 环境变量）
├── db.py                每项目一个 SQLite 引擎（WAL + busy_timeout=5000）、项目目录布局、project_session 依赖
├── domain/              SQLModel 表定义（见「数据模型」）；base.py 提供 utcnow
├── repositories/        next_seq_and_id：max(index)+1 生成 seq 与 id（并发下会撞主键，调用方需重试）
├── api/                 11 个路由模块，逐个对应上表
├── services/
│   ├── generation.py    入队（storyboard/video）、record_artifact、wait_for_jobs、select_and_lock_storyboard、takes_of_shot
│   ├── assets.py        ★ 角色/场景资产服务：建卡落库、出图入队、参考图落盘、锁定、Shot 自动引用、提示词锚点组装
│   └── timeline.py      auto_edit_timeline：按 selected_take 组接时间线
├── jobs/queue.py        ★ 统一生成队列：轮询派发、优先级、VIDEO 信号量、重试、四类 job 执行体
├── agents/
│   ├── base.py          TextModel Protocol + extract_json
│   ├── film_agents.py   ★ 6 个 Agent 的系统提示词与调用函数（全部只依赖 TextModel）
│   ├── mock_llm.py      无 Key 的模板式中文文本模型（离线演示 / 测试）
│   └── openai_compat.py OpenAI 兼容端点适配器
├── generators/
│   ├── base.py          ImageGenerator / VideoGenerator Protocol + 请求/结果模型
│   ├── mock.py          FFmpeg 程序化出图（gradients）/ 出片（testsrc2+噪点+正弦音）
│   └── cloud.py         MiniMax H3、ModelScope API-Inference（图像/视频）
├── graph/film_graph.py  ★ LangGraph 主流程：ProductionState（只存 ID）+ 10 个节点 + 条件边
├── compliance/gate.py   规则式 Prompt 闸门 + 项目级审计日志
└── media/
    ├── ffmpeg.py        make_proxy、probe_duration、probe_has_audio
    └── render.py        render_timeline：分段转码 + concat 成片

frontend/src/
├── App.tsx               工作台布局与路由状态
├── api/client.ts         全部 HTTP 调用（★ 尚无 assets 相关函数）
├── api/types.ts          与后端域模型对应的 TS 类型（★ 尚无 Character/Location）
├── stores/               Zustand 状态
└── components/
    ├── ProjectList.tsx        项目列表 + 一句话生成入口
    ├── ProjectNavigator.tsx   Scene/Shot 导航
    ├── ShotWorkspace.tsx      镜头工作区：分镜候选、Take Pool、选片
    ├── TimelineBar.tsx        时间线
    ├── PreviewPlayer.tsx      ★ 多片段连续播放播放器（自定义控制条 + 全局时间）
    ├── PipelinePanel.tsx      一句话流程阶段面板（5s 轮询）
    ├── AgentActivity.tsx      Agent 产物时间线
    └── Toolbar.tsx            渲染 / 自动剪辑等操作
```

★ = 最近改动集中处。

---

## 数据模型

每个项目一个独立 SQLite：`data/projects/{pid}/project.db`（WAL 模式）。表由 `SQLModel.metadata.create_all` 在首次访问时建好（`db.py` 导入 `domain` 包以注册全部表，**新增表必须在 `domain/__init__.py` 里导出**，否则不会建表）。

| 表 | 关键字段 | 备注 |
|---|---|---|
| `project` | `name`、`idea`、`mode`(AUTO/DIRECTOR)、`status`、`default_take_count` | `status` 即当前阶段名 |
| `scene` | `index`、`title`、`description`、`dramatic_goal` | |
| `shot` | `scene_id`、`index`、`title`、`description`、`duration_target`、`framing`、`camera_motion`、`character_ids`(JSON)、`location_id`、`storyboard_id`、`selected_take_id`、`take_count`、`status` | `character_ids`/`location_id` 由资产锁定后自动回填 |
| `character` | `index`、`name`、`gender`、`age_range`、`role`(PRIMARY/SUPPORTING)、`description`、`appearance`、`costume`、`personality`、`visual_anchors`(JSON)、`immutable_traits`(JSON)、**`prompt_block`**、**`prompt_block_hash`**、**`seed`**、`identity_model_id/type`(LoRA 预留)、`source`(AUTO/IMPORT)、`status`(DRAFT/PENDING_CONFIRM/LOCKED)、`version`、`locked_at` | §13。`prompt_block` 在锁定瞬间编译并固化，之后逐字注入提示词 |
| `location` | `scene_id`、`name`、`description`、`visual_style`、`time_of_day_default`、`materials`、`colors`、`visual_cues`、`immutable_elements`、`lighting_rules`、`prompt_block`、`prompt_block_hash`、`seed`、`source`、`status`、`version`、`locked_at` | §14 |
| `asset` | `type`(CHARACTER_TURNAROUND/LOCATION_REFERENCE/OTHER)、`path`、`proxy_path`、`source`(USER/GENERATED)、`meta`(JSON：`owner_id`/`view`/`model`/`seed`) | 主键是确定性 id `{owner_id}_{view}`（上传为 `{owner_id}_{view}_upload`），**重新生成即覆盖同一行** |
| `storyboard` | `shot_id`、`index`、`prompt`、`image_path`、`width/height`、`model`、`seed`、`status`(CANDIDATE/LOCKED) | |
| `take` | `shot_id`、`index`、`generation_job_id`、`prompt`、`model`、`seed`、`parameters`、`reference_images/videos/audio`、`original_path`、`proxy_path`、`duration` | 抽卡池的最小单位 |
| `timelineclip` | `index`、`take_id`、`in_point`、`out_point`、`volume` | |
| `generationjob` | `shot_id`、`index`、`job_type`、`priority`、`status`、`retry_count`、`payload`(JSON)、`result`(JSON)、`error` | 见下节 |
| `agentartifact` | `index`、`kind`、`agent`、`subject_id`、`data`(JSON) | Agent 产物与阶段记录的统一存储，也是版本追溯的底座 |

ID 形如 `scene_001` / `shot_001` / `char_001` / `loc_001` / `sb_001` / `take_001` / `job_001` / `art_001`，由 `repositories.next_seq_and_id` 按 `max(index)+1` 生成。

---

## 统一生成队列

`backend/app/jobs/queue.py`（§23）。所有生成动作都走它，不直接调模型：

- **进程内 asyncio 队列 + SQLite 持久化**：`_poll_loop` 每 2s 扫描所有项目的 `PENDING` 任务；`notify()` 可立即唤醒。
- **优先级**：`CRITICAL > HIGH > NORMAL > LOW`，同级按创建时间。资产出图入队为 `HIGH`。
- **并发**：只有 `job_type=VIDEO` 受 `video_generation_concurrency` 信号量限制，其余（图像/资产）不限。
- **重试**：`MAX_RETRY = 1`（即最多跑两次），失败写 `error`（截断 500 字）。
- **执行体**：
  - `STORYBOARD` → `_do_storyboard`：调 ImageGenerator 出 N 张候选，逐张写 `storyboard` 行（支持 `references` 参考图透传）。
  - `VIDEO` → `_do_video`：从锁定分镜取首帧 + 从资产链取参考图（≤3 张，FRONT/ESTABLISHING 优先）→ 提交 → 轮询 → 落盘 `takes/{shot_id}/{take_id}.mp4` → 生成 Proxy → 写 `take` 行。
  - `CHARACTER` / `LOCATION` → `_do_asset_images`：按 `views` 逐个视角出图，落盘 `assets/characters|locations/{owner_id}_{view}.png` 并 upsert `asset` 行；全部完成后把资产状态从 `DRAFT` 推到 `PENDING_CONFIRM`。
  - `PIPELINE` 不是队列任务，由 `api/pipeline.py` 直接 `asyncio.create_task` 驱动 LangGraph，只借用 `generationjob` 表记录状态。

**并发安全注意**：`next_seq_and_id` 是「读 max → 写」两步，多个 job 并发时会撞主键。`_do_storyboard` 与 `_do_video` 已加 `IntegrityError` 重试（`ID_ATTEMPTS=3`），`_do_video` 还会先把成片落到 job 专属暂存名 `takes/_staging/{job_id}.mp4`，提交成功后才改名，避免重试覆盖别的 Take 的文件。**新增写 Take/Storyboard/Asset 的路径时请沿用这个模式。**

---

## Agent 体系

基于 LangGraph 1.2.x 编排（`backend/app/graph/film_graph.py`）。生产事实全部结构化存储于 SQLite，Agent 只负责推理、建议、决策与调用工具；大规模数据不进入 Graph State，`ProductionState` 只存 ID 与轻量调度元数据（§46）。

| Agent | 职责 | 关键产出（AgentArtifact.kind） |
|---|---|---|
| Producer | 生产管理、阶段拆解、优先级与预算、Take 数量建议、失败升级 | `production_plan`、`asset_lock` |
| Writer | Idea → Logline → Scene Breakdown → Script | `screenplay` |
| Director | 导演意图、视觉语言、摄影与表演规则、分镜选定、重抽判断（核心 Agent） | `director_bible` |
| 角色设计师 | 剧本 → 角色卡（≤3 个，至少 1 个 PRIMARY，视觉锚点/不可变特征原子化，禁止把 A 的特征写给 B） | `character_cards` |
| 美术指导 | 剧本 → 场景资产（≤3 个，`scene_title` 必须与剧本一致，固定元素/可视锚点/光线规则） | `location_cards` |
| Prompt | ShotSpec + 已锁定角色/场景资产 + Director Bible → 模型 Prompt | 写入 `storyboard.prompt` / `take.prompt` |
| Reviewer | AI Dailies 初筛打分与重抽诊断（辅助审核，不做最终艺术裁决） | `take_review` |
| Editor | 自动剪辑：镜头顺序、In/Out、节奏（转场/BGM/字幕待做） | `editing` |

提示词工程约定（`agents/film_agents.py`，来自真实开源项目的收敛做法，改动前请读注释）：

- Prompt Agent 的组装顺序固定为 **【风格】→【角色】→【场景】→【剧情场景】→【镜头】→【导演圣经】**，不可随意调整。
- 角色「不可变特征」与场景「固定元素」**必须逐字出现**在提示词中；字数超限（400 字）时只能压缩【剧情场景】与【镜头】的动作描述，锚点段落永不删减。
- 单镜头角色数上限 3（`MAX_CHARACTERS_PER_SHOT`），参考图上限 3（`MAX_REFERENCE_IMAGES`，取 DashScope wan2.5-i2i ≤3 与 Phantom ≤4 的更严值）。
- 一致性三重保险：锁定瞬间固化的 `prompt_block`（逐字注入）+ 固定 `seed`（`stable_seed(owner_id)`，md5 前 8 位取模 100000）+ 参考图（有 i2i 后端时）。`identity_model_id/type` 字段为将来 LoRA 预留。

Mock 文本模型（`agents/mock_llm.py`）按系统提示词里的特异关键词路由（顺序：审核 → 提示词工程师 → 分镜候选 → 角色设计师 → 美术指导 → 导演圣经 → 制片 → 剧本），内置 2 个角色预设与 3 个场景预设，创意里点名的角色会被识别进角色卡。**改系统提示词时注意别破坏这些关键词，否则 mock 会返回 `{"text":"mock"}` 导致解析失败。**

---

## Model Agnostic 与生成后端

业务层不绑定任何模型，只依赖四个统一接口（§22：禁止业务代码直接调用具体模型）：

```text
TextModel / VideoUnderstandingModel / ImageGenerator / VideoGenerator
```

其中 **VideoUnderstandingModel 尚未实现**（Reviewer 目前只看文本）。

OpenAI、Claude、Gemini、Qwen、MiniMax、Wan、Z-Image 等均可通过 Adapter 替换。不同 Agent 可分别配置不同模型（当前是全局一个 `llm_backend`，按 Agent 分流是后续工作）。

已实现的后端：

| 接口 | 后端 | 状态 |
|---|---|---|
| TextModel | `mock`（模板式中文） | ✅ 已验收 |
| TextModel | `openai` 兼容（`agents/openai_compat.py`） | 🟡 代码完成，未用真实 Key 联调 |
| ImageGenerator | `mock`（FFmpeg gradients，按 seed 变色） | ✅ 已验收 |
| ImageGenerator | `modelscope`（API-Inference 异步任务） | 🟡 契约已按官方文档核对，未联调 |
| VideoGenerator | `mock`（FFmpeg testsrc2 + 噪点 + 正弦音） | ✅ 已验收 |
| VideoGenerator | `minimax`（H3：提交 → 轮询 → 下载） | 🟡 未联调 |
| VideoGenerator | `modelscope`（Wan2.2-T2V-Fast） | 🟡 未联调 |

**调研结论（角色一致性出图，尚未落地）**：无 LoRA 条件下最可行的参考图路径是 DashScope `wan2.5-i2i-preview`（`input.images` ≤3 张、接受 Base64 `data:{MIME};base64,`、异步提交轮询，与现有 `_CloudVideoBackend` 结构同构），产出首帧后再交给 MiniMax `first_frame_image` 生成 Take；人脸精修可用 `qwen-image-edit-max`。注意：ModelScope API-Inference / MiniMax / CogView-4 **不接受参考图**，VACE 只接受公网 URL（本地优先方案不可用）。这些走 DashScope 而非 ModelScope，需要单独的 `FILMAGENT_DASHSCOPE_API_KEY`（**该假设未验证**）。本地方案：ComfyUI + Phantom-Wan-14B（kijai/ComfyUI-WanVideoWrapper，`--ref_image` ≤4）或 VACE。

---

## 合规节点

生成链路的三个入口（生成分镜、生成 Take、成片渲染出口）均有前置审核：规则式 Prompt 闸门 + 项目级审计日志（`data/projects/{id}/logs/compliance.jsonl`），命中受限内容即阻断并给出分类原因。资产 API 的建卡 / 改卡 / 出图入口同样过闸门（`stage` 分别为 `character.create`、`character.update`、`character.turnaround`、`location.*`）。

一句话全自动流程中，Reviewer Agent 在同一闸门之后对每个 Take 做 AI 初筛评分，未过审镜头自动重拍（最多 `max_auto_retake_rounds` 轮，默认 2），仍不过则按最高分兜底选片并记录兜底原因（`blocked_reason`）供人工复核。

规则表在 `compliance/gate.py` 的 `RULES`（暴力血腥 / 色情低俗 / 违法违规 / 人格侮辱）+ 长度上限 2000 字；这是**非详尽示例词表**，接真实 LLM 审核时在 `ComplianceGate.check` 里扩展。

---

## 项目存储布局

```text
data/
└── projects/{project_id}/
    ├── project.db              该项目全部结构化数据（SQLite，WAL）
    ├── project.json            项目元信息快照（write_project_json）
    ├── assets/
    │   ├── characters/         角色三视图 char_001_FRONT.png / _SIDE / _BACK / _upload
    │   ├── locations/          场景参考图 loc_001_ESTABLISHING.png / _KEY_ANGLE_A/B / _DETAIL
    │   ├── references/         （预留）
    │   ├── audio/              （预留：BGM / 配音）
    │   └── imported_video/     （预留）
    ├── storyboards/            分镜候选图
    ├── takes/
    │   ├── {shot_id}/          成片素材 take_001.mp4
    │   └── _staging/           队列暂存（提交成功后改名移走）
    ├── proxies/                预览代理（Preview 只读这里，不重渲染）
    ├── previews/               （预留）
    ├── renders/                FFmpeg 成片
    ├── snapshots/              （预留：快照 / 版本 diff）
    └── logs/compliance.jsonl   合规审计日志
```

`data/` 已在 `.gitignore` 中，**仓库里不含任何生成产物与数据库**。

---

## 已知问题与踩过的坑

1. **本机 FFmpeg 9.0.1（brew）没有 libass / libfreetype**：`subtitles` 与 `drawtext` 滤镜不可用，字幕**只能软封装**（`mov_text`，编码器可用：mov_text/subrip/ass/webvtt）。`xfade`、`fade`、`overlay`、`amix`、`loudnorm` 都可用，转场与 BGM 混音没有障碍。做 §26 前先按这个能力边界设计。
2. **队列任务卡死 RUNNING**（已修）：`_execute` 的异常分支复用刚 flush 失败的 Session，`IntegrityError` 会升级成 `PendingRollbackError` 逃出协程，job 永远停在 RUNNING，`wait_for_jobs` 干等 900s 超时。修复方式：异常分支先 `session.rollback()` 再 `session.get()` 重新取行。
3. **take/storyboard id 撞主键**（已修）：`next_seq_and_id` 是读-改-写，多 job 并发时会算出同一个 id。已在两处加 `IntegrityError` 重试；`_do_video` 额外用 job 专属暂存名避免重试覆盖他人文件。
4. **HTML5 多片段播放**：切换 `src` 后立刻 `seek` 无效（`readyState` 是旧值），必须在 `loadedmetadata` 里应用；播放意图要用 ref 跨片段保持。`PreviewPlayer.tsx` 已按此实现，别再退回原生 controls 单片段方案（用户会看到「视频只有 3 秒」的假象——每个 proxy 本来就是 3 秒）。
5. **mock 后端的语义**：`MockImageGenerator` 只按 seed 出渐变色，不看 prompt；`MockTextModel` 按关键词路由。所以 Mock 下「角色一致性」体现在**数据链与提示词**上，不体现在像素上——验收要看 `prompt_block` 是否逐字进入 `storyboard.prompt`，不要看图。
6. **前端轮询会被浏览器节流**：标签页 hidden 时 react-query 停止轮询，验证最终态请重新挂载页面（用无头/自动化浏览器时尤其容易误判为「卡住」）。
7. **每项目一个 SQLite 引擎**：`db.get_engine(pid)` 有进程内缓存，删除项目要调 `drop_engine`。跨项目查询不存在，列表页是遍历 `data/projects/*/project.db`。
8. **LangGraph 节点会从头部重跑**：`interrupt()` 恢复时同一节点会重新执行（实测计数器 +1），所以人工介入必须放在**独立的 gate 节点**里，且绝不能排在 DB 写入 / 入队之后。这是 §24 的实现前提。

---

## 未完成部分（按优先级）

> 括号内是已完成的调研结论，接手时可直接照做，不必重新调研。

### P0 — 资产链前端（后端已就绪，只差 UI）

`backend/app/api/assets.py` 全部接口可用，`GET /assets` 已返回渲染所需的一切。缺：

- `frontend/src/api/types.ts`：`Character` / `Location` / `AssetReference` 类型
- `frontend/src/api/client.ts`：`fetchAssets`、`lockCharacter`、`lockLocation`、`generateCharacterRefs`、`generateLocationRefs`、`uploadCharacterRef`、`uploadLocationRef`、`patchCharacter`、`patchLocation`、`relinkShots`
- 新组件（建议 `components/AssetPanel.tsx`）：角色卡 / 场景卡列表，展示三视图与参考图缩略图、`status` 徽标（DRAFT/PENDING_CONFIRM/LOCKED）、`prompt_block` 折叠查看、「确认并锁定」与「重新生成」按钮；挂进 `ShotWorkspace` 或工作台侧栏
- 验收：AUTO 流程跑完后能在 UI 里看到 1~2 个角色 + 3 个场景已锁定，点开能看到三视图

### P0 — 队列并发修复的回归验证

本轮修了 `_execute` 回滚、`_do_storyboard` / `_do_video` 的 id 重试，**尚未跑完整 E2E 回归**。接手第一件事：新建 AUTO 项目跑一次 `POST /one-sentence`，确认 12 个 Take 全部 DONE、无 job 卡 RUNNING、`PIPELINE` 到 `COMPLETE` 且有 `render_url`。方法见[验收与自测方法](#验收与自测方法)。

### P1 — VideoUnderstandingModel + Reviewer 真实看片（§11）

当前 `review_take` 只把 prompt 和 take_id 丢给文本模型，评分是模板值。调研结论：

- **适配器要保持"笨"**：`VideoUnderstandingModel.describe(VideoUnderstandingRequest) -> VideoInsight`（字段：`model`、`frame_count`、`timeline[{t,text}]`、`observation`、`raw`），**打分逻辑留在 Reviewer**，不要让 VLM 直接返回分数结构。
- **抽帧**：均匀 8 帧、保留首尾、`max_width=896`、JPEG q≈80、相邻帧 MAE 去重；contact sheet（`ffmpeg -vf tile=4x2`）只作补充。
- **托管 DashScope 必须用 base64 的 `image_url` 分片，不能用 `video_url`**（云端拉不到本机 localhost）；`image_url` 模式没有 `fps` 字段，时间信息要写进文本。本地 Qwen3-VL 可以走 `{"type":"video","video":<本地路径>}` + `fps`/`num_frames`。
- **混合 CV（无 ML 依赖，先跑）**：相邻帧 MAE 算 `temporal_consistency`、Farneback 光流算 `motion_quality`，加一组确定性硬失败闸门（连续黑帧 >2、冻结帧占比 >35%、时长误差 >200ms、解码失败）——**这些必须在任何付费调用之前跑完**。VLM 只负责 `narrative_intent` / `continuity` / `issues`。
- **评分表**：每维度用分级二元 checklist，**模型不返回总分，由应用侧加权求和**；证据优先（先逐帧走查再给分）；0-100 整数；`temperature=0`；本地量化模型加 `repetition_penalty≈1.05`。KEEP/RETAKE 用绝对阈值，但**选优用两两比较**（VLM 排序能力远好于打分能力）。
- **队列**：新增 `job_type="REVIEW"`，用**自己的信号量（2~4）**，不要复用 VIDEO 的；图内重拍循环里直接 await。
- 本地部署：NGC vLLM 容器跑 Qwen3-VL，暴露 OpenAI 兼容端点。未验证项：base64 能否放进 `type:"video"` 数组（按不支持处理）、GLM-4.5V 视频、MiniMax 没有视频理解 VLM（别指望它做 Reviewer）。

### P1 — 实时事件推送（替换 5s 轮询）

调研结论（已对本仓库安装的 LangGraph 1.2.11 验证）：

- **用 SSE（`sse-starlette`）而不是 WebSocket**：单向推送足够，断线重连由浏览器负责。
- 图必须由**后端自有的 `asyncio.Task`** 驱动（现在就是），浏览器刷新不能杀掉 20 分钟的渲染。
- 扇出 hub：每项目一个 `asyncio.Queue`，发送要加守卫，带心跳 ping。
- 新增 `pipeline_event` 表，带单调 `seq`，支持 `?since_seq=` 断点补发；**保留轮询作为降级**（5000ms）。
- 流式：`stream_mode=["tasks","updates","custom"]` 产出 `(mode, chunk)` 元组；`tasks` 免费给出节点开始/结束/错误/中断；自定义事件用 `langgraph.config.get_stream_writer()`（`langgraph.types.StreamWriter` 只是类型别名）。

### P1 — Director Mode（interrupt / resume 人工介入）

调研结论（已验证）：

- `interrupt()` + `Command(resume=)`，从 `langgraph.types` 导入。
- **节点跨 interrupt/resume 会从头重跑**（实测计数器 = 2）→ 把 `interrupt()` 放进**独立的 gate 节点**：`approve_script`、`approve_assets`、`approve_storyboard`、`resolve_block`、`approve_selection`，以 `state["mode"] != "AUTO"` 为条件；**绝不要**排在 DB 写入或入队之后。
- Checkpointer：`AsyncSqliteSaver(conn)` **不是** async context manager（只有 `from_conn_string` 是），它会捕获 `get_running_loop()`，因此必须在 lifespan 里构建；需要按项目做 registry；关停时要 `conn.close()`，否则 aiosqlite 在 loop 拆除时抛错。与 project.db 共库在 WAL 下可行，但**建议单独一个 `checkpoints.db`**。
- `durability="sync"`（我们的节点是分钟级；默认是 `"async"`，绝不要用 `"exit"`）。
- `thread_id = f"{project_id}:{pipeline_job_id}"`。
- 进程被硬杀后新进程恢复：**已验证**不会重跑已完成节点。启动时用 `snap = await graph.aget_state(cfg); if snap.next: await graph.ainvoke(None, cfg)` 重新驱动。
- 需要装：`langgraph-checkpoint-sqlite==3.1.1`、`aiosqlite`、`sse-starlette`。
- 还要加：`POST /pipeline/resume`、`GET /pipeline/state`。

### P2 — 快照 / 版本 diff（§ 快照）

LangGraph 的 time-travel **不能**替代业务版本管理：`AgentArtifact` 继续作为版本真相源。要做的：给 `AgentArtifact` 加 `run_id` 与 `version` 两列，增加一种 `kind='run_snapshot'` 的行打包 `{checkpoint_id, thread_id, artifact_version_map}`，再补一个 diff 接口。`data/projects/{pid}/snapshots/` 目录已预留。

### P2 — Editor 转场 / 字幕 / BGM

在[已知问题 1](#已知问题与踩过的坑) 的 FFmpeg 能力边界内做：`xfade` 转场、`mov_text` 软字幕（**不要试图烧字幕**）、`amix` + `loudnorm` 混 BGM。`media/render.py` 现在是「分段转码 + concat」，改造点在 `_segment_cmd` 与 `render_timeline`。

### P2 — 自动化测试

仓库无 `tests/`。优先补：`services/assets.py` 的纯函数（`character_prompt_block`、`location_prompt_block`、`stable_seed`、`link_shots_to_assets` 的匹配规则）、队列的 id 冲突重试、合规闸门、`auto_edit_timeline`。这些都不需要 FFmpeg 之外的外部依赖。

### P3 — 比赛交付项（截止约 2026-09-15）

- 真实云 Key 联调：DashScope `wan2.5-i2i-preview`（参考图，需单独 Key）、MiniMax `first_frame_image`、ModelScope 图像/视频
- DGX Spark 部署：ComfyUI + WanVideoWrapper（Phantom-Wan-14B / VACE）、vLLM 跑 Qwen3-VL（Blackwell sm_121 有风险，FP8 需 cu130-nightly-aarch64）；节点 `ssh spark-57`，归还前要清数据与权重
- 微调 / 量化（评分项「技术创新与模型工程」）
- 参赛短片内容制作
- 复现 Notebook / 创空间（评分项「复现开源部署」）

---

## 验收与自测方法

```bash
cd backend

# 0) 后端起在 8765（前端默认端口）
.venv/bin/uvicorn app.main:app --port 8765 &

# 1) 建项目（AUTO 模式）
curl -s -X POST http://127.0.0.1:8765/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"验收","idea":"雨夜天文台，少年林澈与守塔人苏晚的告别","mode":"AUTO"}'
# 记下返回的 id，下面用 $PID 代替

# 2) 启动一句话全自动流程
curl -s -X POST "http://127.0.0.1:8765/api/v1/projects/$PID/one-sentence"

# 3) 轮询阶段（期望依次出现 SCRIPTING → ASSET_DESIGN → STORYBOARDING
#    → VIDEO_GENERATION → REVIEWING →(可能回到 VIDEO_GENERATION)→ TAKE_SELECTION
#    → EDITING → PREVIEW → RENDERING → COMPLETE，job.status=DONE 且有 render_url）
watch -n5 "curl -s http://127.0.0.1:8765/api/v1/projects/$PID/pipeline"

# 4) 资产链验收点
curl -s "http://127.0.0.1:8765/api/v1/projects/$PID/assets"
#   - characters/locations 均为 LOCKED，有 prompt_block、prompt_block_hash、seed
#   - 每个角色 3 张三视图、每个场景 4 张参考图，media_url 可直接打开
curl -s "http://127.0.0.1:8765/api/v1/projects/$PID/shots"
#   - 每个 shot 的 character_ids 非空、location_id 指向对应场景
curl -s "http://127.0.0.1:8765/api/v1/projects/$PID/shots/shot_001/storyboards"
#   - prompt 里能看到【风格】【角色】【场景】【镜头】【导演圣经】五段，
#     且「不可变特征」「固定元素」逐字出现

# 5) 队列健康（不应该有长期 RUNNING 的 job）
curl -s "http://127.0.0.1:8765/api/v1/projects/$PID/generation-jobs"

# 6) 成片
open "http://127.0.0.1:8765/media/$PID/renders/<render 文件名>"
```

排查入口：后端日志（uvicorn stdout）、`data/projects/{pid}/logs/compliance.jsonl`、`generationjob.error` 字段、`agentartifact` 里 `kind='stage'` 的 note。

---

## 路线图

- [x] **阶段 0**：系统设计与规格（[项目概述.md](项目概述.md)）
- [x] **阶段 1**：底层生产链（无 AI）——Project → Shot → 上传 / 添加 Take → 选片 → Timeline → 实时 Preview → FFmpeg 渲染
- [x] **阶段 2**：ImageGenerator / VideoGenerator Adapter、Storyboard（候选 / 选定 / 锁定）、统一生成队列、Mock + 云 API 后端
- [x] **阶段 3**：LangGraph 主流程 + 全部 Agent——一句话创意全自动生产（剧本 → 资产 → 分镜 → Take 生成 → 审核重拍 → 选片 → 剪辑 → 成片）
- [x] **阶段 3.5**：角色 / 场景资产链后端（Character Card、三视图、参考图、锁定与提示词锚点、Shot 自动引用）
- [ ] **阶段 4**：资产链前端 + VideoUnderstandingModel（Reviewer 真实看片）+ SSE 实时事件
- [ ] **阶段 5**：Director Mode（interrupt / resume）+ 快照 / 版本 diff + Editor 转场字幕 BGM
- [ ] **阶段 6**：pytest 覆盖 + 真实云 Key 联调 + DGX Spark 部署 + 微调 / 量化
- [ ] **阶段 7**：参赛短片 + 复现 Notebook / 创空间 + 桌面打包（Tauri，预留）

> 实施原则（§70）：先保证 `Shot → Take → Select → Timeline → Preview` 底层生产链路成立，再让 Agent 接入这条链路，而非先做复杂 Agent。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + Vite + TypeScript + Zustand + TanStack Query（实时推送待接 SSE） |
| 后端 | Python 3.11+ + FastAPI + Pydantic v2 + SQLModel + SQLite（每项目一库，WAL） |
| Agent | LangGraph 1.2.x（工作流状态、分支、重试；interrupt/resume 待接） |
| 生成运行时 | Mock（FFmpeg 程序化）/ MiniMax / ModelScope；ComfyUI + DGX Spark 为后续内部执行层 |
| 媒体 | FFmpeg 9.x（Proxy 生成、Final Render；无 libass，字幕走软封装） |
| 打包（预留） | Tauri → macOS .app / Windows .exe / Linux |

实测版本：fastapi 0.141.1、starlette 1.6.0、langgraph 1.2.11、langgraph-checkpoint 4.2.0、langchain-core 1.6.1、pydantic 2.13.5、sqlmodel 0.0.42、python 3.14.6。

## 文档

- [项目概述.md](项目概述.md) — 系统设计与开发规格书 v1.0：完整定义数据结构、API、事件、项目存储、快照、错误处理、测试计划与端到端验收标准（本 README 中「§N」均指此文件）
