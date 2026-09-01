"""合规节点：生成前置审核（规格补丁，对应评审「合规机制」）。

规则式 Prompt 审核 + 项目级审计日志；LLM 审核可后续在 review() 扩展。
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from ..db import project_path

# 非详尽示例词表：命中即拒绝，MVP 阶段用于演示审核闭环
RULES: list[tuple[str, list[str]]] = [
    ("暴力血腥", ["血腥", "斩首", "肢解", "酷刑", "虐待儿童"]),
    ("色情低俗", ["色情", "裸体性爱", "成人影片", "嫖娼"]),
    ("违法违规", ["制造炸弹", "制毒", "枪械改造", "诈骗教程"]),
    ("人格侮辱", ["辱骂", "人肉搜索", "恶意诽谤"]),
]

MAX_PROMPT_LENGTH = 2000


class ComplianceResult(BaseModel):
    allowed: bool
    category: str | None = None
    reason: str | None = None

    def detail(self) -> str:
        return f"compliance_blocked[{self.category}]: {self.reason}"


class ComplianceGate:
    """生成前 Prompt 审核。策略：规则命中即拒绝，全部通过才放行。"""

    def check(self, prompt: str) -> ComplianceResult:
        text = (prompt or "").strip()
        if not text:
            return ComplianceResult(allowed=True)
        if len(text) > MAX_PROMPT_LENGTH:
            return ComplianceResult(
                allowed=False,
                category="超长",
                reason=f"提示词长度 {len(text)} 超过 {MAX_PROMPT_LENGTH}",
            )
        for category, keywords in RULES:
            for keyword in keywords:
                if keyword and keyword in text:
                    return ComplianceResult(
                        allowed=False,
                        category=category,
                        reason=f"提示词包含受限内容「{keyword}」",
                    )
        return ComplianceResult(allowed=True)

    def check_or_raise(self, prompt: str, stage: str, project_id: str) -> None:
        result = self.check(prompt)
        self.audit(project_id, stage, prompt, result)
        if not result.allowed:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=result.detail())

    def audit(
        self, project_id: str, stage: str, prompt: str, result: ComplianceResult
    ) -> None:
        try:
            log_dir = project_path(project_id) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "prompt": prompt[:500],
                "allowed": result.allowed,
                "category": result.category,
                "reason": result.reason,
            }
            with (log_dir / "compliance.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


compliance_gate = ComplianceGate()

__all__ = ["ComplianceGate", "ComplianceResult", "compliance_gate"]
