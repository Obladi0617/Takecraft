# AI Film Agent

> 本地优先（Local-first）的多智能体 AI 短片生产工作流系统

这不是一个 "Prompt → Video" 的一步出片工具。

它把一套完整的 AI 影视生产方法，固化为**可持续运行、可暂停、可修改、可回溯、可重新生成**的多 Agent 工作流：真实影视工业中的策划、编剧、导演、分镜、Dailies 审核、选片与剪辑，被重新映射为 Agent 规划、资产锁定、Storyboard、生成、审核、选择、Timeline 与渲染。人在任何关键创作节点都可以介入。

## 核心差异点：Take 抽卡

本系统最重要的产品与数据设计是：

> 把生成结果作为**可管理、可审核、可比较、可选择、可重新生成**的 Take。

```text
Project → Scene → Shot → Storyboard → Take[] → selected_take → Timeline
```

围绕这条数据链，系统提供：

- 每个 Shot 生成任意数量的 Take，进入 Take Pool 并排比较
- Reviewer Agent 对每个 Take 多维打分：Prompt 遵循度、角色一致性、时序一致性、运动质量、构图、叙事意图、连贯性
- 有限自动重抽（默认最多 2 轮），超出即 interrupt 交还用户决策
- 用户随时切换 selected_take，Timeline 引用与实时 Preview 立即生效
- 每个 Take 的 Prompt / 模型 / Seed / 参数全程留痕，支持版本 diff 与追溯

## 工作流总览

```text
Idea / 人机讨论
        ↓
剧本（Writer）
        ↓
角色 / 场景资产（Card + 三视图 / 参考图，锁定）
        ↓
导演方案（Director Bible）
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

### 两种工作模式

- **Auto Mode**：一句话创意进入，Agent 最大程度自主完成全流程；仅在生成连续失败、超出自动重抽上限、必要资产缺失、严重模型异常时强制 interrupt
- **Director Mode**：用户可在任意阶段介入——修改剧本 / Shot / Prompt、重抽 Storyboard、锁定分镜、设置 Take 数量、选择 Take、修改 Timeline、覆盖 Editor 决策

## Agent 体系

基于 LangGraph 编排。生产事实全部结构化存储于 SQLite，Agent 只负责推理、建议、决策与调用工具；大规模数据不进入 Graph State，按 ID 经 Repository 读取。

| Agent | 职责 | 关键产出 |
|---|---|---|
| Producer | 生产管理、阶段拆解、优先级与预算、Take 数量建议、失败升级 | ProductionPlan |
| Writer | Idea → Logline → Synopsis → Scene Breakdown → Script | 结构化 SceneSpec |
| Director | 导演意图、视觉语言、摄影与表演规则、镜头设计、重抽判断（核心 Agent） | DirectorBible |
| Prompt | ShotSpec + 角色 / 场景资产 + Director Bible → 具体模型 Prompt | 带版本历史的 Prompt |
| Reviewer | AI Dailies 初筛打分与重抽诊断（辅助审核，不做最终艺术裁决） | TakeReview |
| Editor | 自动剪辑：镜头顺序、In/Out、节奏、转场、BGM、字幕 | Timeline |

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React + Vite + TypeScript + Zustand + TanStack Query + WebSocket |
| 后端 | Python 3.11+ + FastAPI + Pydantic + SQLModel + SQLite |
| Agent | LangGraph（工作流状态、分支、interrupt / resume、重试） |
| 生成运行时 | ComfyUI（内部可选执行层，DGX Spark 承担视频生成） |
| 媒体 | FFmpeg（Proxy 生成、Final Render） |
| 打包（预留） | Tauri → macOS .app / Windows .exe / Linux |

### Model Agnostic

业务层不绑定任何模型，只依赖四个统一接口：

```text
TextModel / VideoUnderstandingModel / ImageGenerator / VideoGenerator
```

OpenAI、Claude、Gemini、Qwen、MiniMax、Wan、Z-Image 等均可通过 Adapter 替换，无需改动核心业务层。不同 Agent 可分别配置不同模型。

### 云本地协同（后端可切换）

生成后端通过环境变量切换，业务层只依赖统一接口（规格书第 22 节：禁止业务代码直接调用具体模型）：

```bash
FILMAGENT_IMAGE_BACKEND=modelscope   # mock | modelscope
FILMAGENT_VIDEO_BACKEND=minimax      # mock | minimax | modelscope
FILMAGENT_MINIMAX_API_KEY=...        # MiniMax H3 API
FILMAGENT_MODELSCOPE_API_KEY=...     # ModelScope API-Inference
```

- **mock**：无 GPU 环境的本地后端（FFmpeg 程序化生成图像 / 视频），保证全链路可离线演示与测试
- **minimax**：MiniMax H3（任务式提交 → 轮询 → 下载）
- **modelscope**：ModelScope API-Inference（异步任务协议，图像已按官方契约验证）

### 合规节点

生成链路的三个入口（生成分镜、生成 Take、成片渲染出口）均有前置审核：规则式 Prompt 闸门 + 项目级审计日志（`data/projects/{id}/logs/compliance.jsonl`），命中受限内容即阻断并给出分类原因。一句话全自动流程中，Reviewer Agent 在同一闸门之后对每个 Take 做 AI 初筛评分，未过审镜头自动重拍（最多 `max_auto_retake_rounds` 轮，默认 2），仍不过则按最高分兜底选片并记录兜底原因供人工复核。

## 快速开始

```bash
# 后端（端口 8765）
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8765

# 前端（端口 5173）
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`，在「一句话生成短片」输入创意并点击启动，即可全自动完成：剧本 → 导演圣经 → 分镜候选 → Take 生成 → 审核（自动重拍）→ 选片 → 剪辑 → 成片渲染。右侧「一句话全自动流程」面板实时显示各阶段进度。

默认使用 Mock 文本模型与 Mock 生成后端（无需任何 API Key / GPU）。切换真实后端见「云本地协同」。

## 路线图

- [x] **阶段 0**：系统设计与规格（[项目概述.md](项目概述.md)）
- [x] **阶段 1**：底层生产链（无 AI）——Project → Shot → 上传 / 添加 Take → 选片 → Timeline → 实时 Preview → FFmpeg 渲染
- [x] **阶段 2**：ImageGenerator / VideoGenerator Adapter、Storyboard（候选 / 选定 / 锁定）、统一生成队列、Mock + 云 API 后端
- [x] **阶段 3**：LangGraph 主流程 + 全部 6 个 Agent——一句话创意全自动生产（剧本 → 分镜 → Take 生成 → 审核重拍 → 选片 → 剪辑 → 成片）

> 实施原则（规格书第 70 节）：先保证 `Shot → Take → Select → Timeline → Preview` 底层生产链路成立，再让 Agent 接入这条链路，而非先做复杂 Agent。

## 文档

- [项目概述.md](项目概述.md) — 系统设计与开发规格书 v1.0：完整定义数据结构、API、WebSocket 事件、项目存储、快照、错误处理、测试计划与端到端验收标准
