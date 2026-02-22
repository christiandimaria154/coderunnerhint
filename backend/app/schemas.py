from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class CodeRunnerPayload(BaseModel):
    score: float = 0.0
    max_score: float = 1.0
    compile_error_text: str = ""
    runtime_error_text: str = ""
    failed_tests: list[str] = Field(default_factory=list)
    full_feedback_text: str = ""


class HintRequest(BaseModel):
    mode: str = "training"  # training|exam
    language: str = "c"
    course_id: int = 0
    quiz_id: int = 0
    question_id: int = 0
    question_slot: int = 0
    question_name: str = ""
    student_id: str = "anon"
    attempt_id: int = 0
    attempt_no: int = 0
    source_code: str = ""
    coderunner: CodeRunnerPayload
    plugin_meta: dict[str, Any] = Field(default_factory=dict)


class HintResponse(BaseModel):
    enabled: bool = True
    hint_level: int = 1
    hint_type: str = "generic"
    cluster_key: str = "c_generic"
    hint_text: str = ""
    confidence: float = 0.0
    hint_variant: str = "default"
    learning: Optional[dict[str, Any]] = None
