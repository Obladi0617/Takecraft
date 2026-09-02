"""视觉理解模型接口：Reviewer 真实看片（规格补丁 VideoUnderstandingModel）。

契约要点：
1. 模型只输出**分项**评分与观察，总分与 KEEP/RETAKE 判定一律在应用侧算
   （模型给总分会把权重黑箱化，也无法与 ffmpeg 的确定性指标加权融合）；
2. 分项分两类——`cv_*` 由 ffmpeg 统计量确定性算出，其余由视觉模型打分；
3. 抽帧是接口的一部分：调用方给帧路径，适配器自己决定怎么编码上传。
"""

from typing import Protocol

from pydantic import BaseModel, Field


class ChecklistSpec(BaseModel):
    key: str
    label: str
    guide: str
    weight: float
    graded_by: str = "model"  # model | cv


CHECKLIST: list[ChecklistSpec] = [
    ChecklistSpec(
        key="prompt_alignment",
        label="提示词契合",
        guide="画面主体、动作、环境是否与提示词描述一致；缺主体或主体错位要重扣",
        weight=0.15,
    ),
    ChecklistSpec(
        key="character_consistency",
        label="角色一致性",
        guide="出场角色的外观是否与给定不可变锚点一致（发型、服饰、显著特征）；"
        "无角色出场时按画面主体是否稳定判分",
        weight=0.12,
    ),
    ChecklistSpec(
        key="temporal_consistency",
        label="时序一致性",
        guide="帧与帧之间主体/场景是否连续，有无突变、肢体或物体畸变、多出来的手脚",
        weight=0.12,
    ),
    ChecklistSpec(
        key="composition",
        label="构图景别",
        guide="景别与构图是否符合镜头设定，主体位置、留白、视线引导是否成立",
        weight=0.10,
    ),
    ChecklistSpec(
        key="motion_quality",
        label="运动质量",
        guide="运动是否自然、符合运镜设定，有无漂浮、卡顿感、非物理运动",
        weight=0.08,
    ),
    ChecklistSpec(
        key="narrative_intent",
        label="叙事意图",
        guide="画面是否传达了该镜头在剧情中的情绪与意图",
        weight=0.08,
    ),
    ChecklistSpec(
        key="continuity",
        label="跨镜连续性",
        guide="光线、色调、天气、时间是否与整体视觉风格一致（不要求与相邻镜头逐帧匹配）",
        weight=0.05,
    ),
    ChecklistSpec(
        key="cv_duration",
        label="时长匹配",
        guide="实际时长与镜头设定时长的偏差",
        weight=0.10,
        graded_by="cv",
    ),
    ChecklistSpec(
        key="cv_exposure",
        label="曝光可用",
        guide="黑帧/过曝比例",
        weight=0.08,
        graded_by="cv",
    ),
    ChecklistSpec(
        key="cv_motion",
        label="画面活动度",
        guide="静止帧比例与平均运动量",
        weight=0.08,
        graded_by="cv",
    ),
    ChecklistSpec(
        key="cv_stability",
        label="亮度稳定性",
        guide="逐帧亮度抖动（闪烁）",
        weight=0.04,
        graded_by="cv",
    ),
]

MODEL_CHECKLIST = [c for c in CHECKLIST if c.graded_by == "model"]
CV_CHECKLIST = [c for c in CHECKLIST if c.graded_by == "cv"]
CHECKLIST_BY_KEY = {c.key: c for c in CHECKLIST}


class VideoFrame(BaseModel):
    index: int
    time: float
    path: str
    digest: str = ""  # 帧内容摘要：判重 + 落库留证（证明送审帧确实不同）


class VideoUnderstandingRequest(BaseModel):
    take_id: str
    shot_title: str
    prompt: str
    expected_duration: float = 3.0
    framing: str = "MEDIUM"
    camera_motion: str = "STATIC"
    frames: list[VideoFrame] = Field(default_factory=list)
    cv_summary: str = ""
    character_anchors: list[str] = Field(default_factory=list)
    location_anchors: list[str] = Field(default_factory=list)


class ChecklistAnswer(BaseModel):
    key: str
    score: int = 0
    note: str = ""


class TimelineNote(BaseModel):
    t: float
    text: str


class VideoInsight(BaseModel):
    """模型看片结论。注意：不含总分、不含 decision——那是应用侧的职责。"""

    model: str = ""
    frame_count: int = 0
    observation: str = ""
    narrative_intent: str = ""
    continuity: str = ""
    timeline: list[TimelineNote] = Field(default_factory=list)
    checklist: list[ChecklistAnswer] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggested_prompt_changes: list[str] = Field(default_factory=list)
    raw: str = ""

    def scores(self) -> dict[str, int]:
        return {item.key: max(0, min(100, int(item.score))) for item in self.checklist}


class CompareVerdict(BaseModel):
    winner: str = "TIE"  # A | B | TIE
    reason: str = ""
    confidence: int = 50


class VideoUnderstandingModel(Protocol):
    """统一视觉理解接口（Model Agnostic：云端 VLM 与本地量化模型同一套调用）。"""

    name: str

    async def describe(self, request: VideoUnderstandingRequest) -> VideoInsight: ...

    async def compare(
        self,
        shot_title: str,
        prompt: str,
        a: list[VideoFrame],
        b: list[VideoFrame],
    ) -> CompareVerdict: ...


DESCRIBE_SYSTEM = f"""你是 AI Dailies 审核员，正在逐帧查看一条生成镜头的抽帧序列。
你需要对下列 {len(MODEL_CHECKLIST)} 个分项各给 0-100 的整数分与一句中文理由：
{chr(10).join(f"- {c.key}（{c.label}）：{c.guide}" for c in MODEL_CHECKLIST)}

硬性要求：
1. 只评你能在画面里看到的东西，看不清就按「证据不足」给中间分并在 note 里说明；
2. 不要输出总分、不要输出 KEEP/RETAKE 决策——判定由生产系统按权重计算；
3. 抽帧按时间顺序给出，请结合帧间变化判断时序与运动，不要只看单帧。

输出 JSON：
{{"observation": "整体画面一句话描述", "narrative_intent": "是否传达剧情意图",
"continuity": "光线色调与整体风格是否连贯",
"timeline": [{{"t": 0.0, "text": "该时刻画面发生了什么"}}],
"checklist": [{{"key": "<分项key>", "score": 0, "note": "<中文理由>"}}],
"issues": ["具体问题"], "suggested_prompt_changes": ["下一轮重拍时提示词该怎么改"]}}
中文。只输出 JSON。"""

COMPARE_SYSTEM = """你是导演，正在为同一个镜头在两条已通过初筛的 Take 之间做最终选择。
两组抽帧按时间顺序给出（A 组在前、B 组在后）。请比较表演/运动自然度、构图、
与提示词的契合度，选出更适合进入成片的一条。
输出 JSON：{"winner": "A|B|TIE", "reason": "中文一句话理由", "confidence": 0-100}
只在两条确实难分高下时输出 TIE。只输出 JSON。"""


def build_describe_user(request: VideoUnderstandingRequest) -> str:
    """把镜头设定、资产锚点与 ffmpeg 测量值拼成事实性上下文（不含任何评分）。"""
    lines = [
        f"镜头：{request.shot_title}",
        f"景别 {request.framing}；运镜 {request.camera_motion}；设定时长 {request.expected_duration:.2f}s",
        f"提示词：{request.prompt}",
    ]
    if request.character_anchors:
        lines.append("角色不可变锚点（必须逐字出现在画面里）：" + "、".join(request.character_anchors))
    if request.location_anchors:
        lines.append("场景固定元素：" + "、".join(request.location_anchors))
    if request.cv_summary:
        lines.append(f"技术测量：{request.cv_summary}")
    lines.append(
        f"以下 {len(request.frames)} 张抽帧按时间顺序排列："
        + "、".join(f"#{i + 1}@{f.time:.2f}s" for i, f in enumerate(request.frames))
    )
    return "\n".join(lines)


def build_compare_user(
    shot_title: str, prompt: str, a: list[VideoFrame], b: list[VideoFrame]
) -> str:
    return (
        f"镜头：{shot_title}\n提示词：{prompt}\n"
        f"A 组 {len(a)} 帧（"
        + "、".join(f"{f.time:.2f}s" for f in a)
        + f"）\nB 组 {len(b)} 帧（"
        + "、".join(f"{f.time:.2f}s" for f in b)
        + "）\n先看完 A 组再看完 B 组，然后给出选择。"
    )


__all__ = [
    "CHECKLIST",
    "CHECKLIST_BY_KEY",
    "CV_CHECKLIST",
    "MODEL_CHECKLIST",
    "ChecklistAnswer",
    "ChecklistSpec",
    "CompareVerdict",
    "TimelineNote",
    "VideoFrame",
    "VideoInsight",
    "VideoUnderstandingModel",
    "VideoUnderstandingRequest",
    "build_compare_user",
    "build_describe_user",
]
