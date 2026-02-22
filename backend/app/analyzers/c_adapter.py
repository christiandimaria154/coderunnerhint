from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from app.schemas import HintRequest


@dataclass
class AnalysisResult:
    language: str
    cluster_key: str
    hint_type: str
    confidence: float
    signals: dict[str, Any]


class CAdapter:
    """C-first heuristic analyzer for CodeRunner feedback + submitted code.

    Goal (MVP): classify *didactically useful* issues and avoid wasting hints on trivial syntax.
    """

    # Compile patterns worth tutoring (not pure syntax punctuation).
    COMPILE_PATTERNS = [
        (r"undeclared(?:\s+identifier)?|was not declared|implicit declaration", "c_undeclared_identifier", "compile_symbol", 0.92),
        (r"conflicting types for|incompatible type|incompatible pointer type", "c_type_mismatch", "types", 0.9),
        (r"too (?:few|many) arguments to function|passing argument .* from incompatible pointer type", "c_parameter_mismatch", "signature", 0.9),
        (r"conflicting types for .*|previous declaration of .* with type", "c_prototype_conflict", "prototype", 0.9),
        (r"return type .* is not compatible|return makes .* from .* without a cast", "c_return_type_mismatch", "return_type", 0.86),
        (r"subscripted value is neither array nor pointer|invalid type argument of unary \*", "c_pointer_deref_misuse", "pointers", 0.88),
        (r"free\(|invalid conversion .*free", "c_free_misuse_compile", "memory", 0.76),
        (r"warning: unused variable", "c_warning_unused_variable", "warning_unused", 0.55),
    ]

    RUNTIME_PATTERNS = [
        (r"segmentation fault|sigsegv", "c_segfault", "runtime_memory", 0.95),
        (r"addresssanitizer.*heap-use-after-free|use-after-free", "c_use_after_free", "memory", 0.98),
        (r"addresssanitizer.*double-free|double free", "c_double_free", "memory", 0.98),
        (r"addresssanitizer.*invalid free|free\(\): invalid pointer", "c_invalid_free", "memory", 0.97),
        (r"stack-buffer-overflow|heap-buffer-overflow|out of bounds", "c_out_of_bounds", "bounds", 0.94),
        (r"null pointer|dereference of null|addresssanitizer.*null", "c_null_dereference", "pointers", 0.92),
    ]

    # Logic heuristics from failed tests / code shape / feedback text.
    def analyze(self, req: HintRequest) -> AnalysisResult:
        code = req.source_code or ""
        cr = req.coderunner
        compile_text = (cr.compile_error_text or "")
        runtime_text = (cr.runtime_error_text or "")
        full_text = (cr.full_feedback_text or "")
        failed_tests = cr.failed_tests or []

        merged_compile = (compile_text + "\n" + full_text).lower()
        merged_runtime = (runtime_text + "\n" + full_text).lower()
        failed_join = "\n".join(failed_tests).lower()

        signals: dict[str, Any] = {
            "compile_patterns": [],
            "runtime_patterns": [],
            "failed_test_cues": [],
            "code_features": self._extract_code_features(code),
            "score_ratio": self._safe_ratio(cr.score, cr.max_score),
        }

        # 1) Runtime memory issues first.
        for pat, cluster, hint_type, conf in self.RUNTIME_PATTERNS:
            if re.search(pat, merged_runtime, flags=re.I | re.S):
                signals["runtime_patterns"].append(cluster)
                return AnalysisResult(
                    language="c",
                    cluster_key=cluster,
                    hint_type=hint_type,
                    confidence=conf,
                    signals=signals,
                )

        # 2) Compile but conceptually useful.
        for pat, cluster, hint_type, conf in self.COMPILE_PATTERNS:
            if re.search(pat, merged_compile, flags=re.I | re.S):
                signals["compile_patterns"].append(cluster)
                # De-prioritize pure warning-only situations when score is already full.
                if cluster == "c_warning_unused_variable" and signals["score_ratio"] >= 0.99:
                    break
                return AnalysisResult(
                    language="c",
                    cluster_key=cluster,
                    hint_type=hint_type,
                    confidence=conf,
                    signals=signals,
                )

        # 3) Logic/case-limit cues from failed tests.
        if failed_join:
            if any(k in failed_join for k in ["empty", "vuoto", "n=0", "zero"]):
                signals["failed_test_cues"].append("edge_case_empty")
                return AnalysisResult("c", "c_logic_edge_case_empty", "edge_case", 0.8, signals)
            if any(k in failed_join for k in ["single", "uno", "1 elemento", "one element"]):
                signals["failed_test_cues"].append("edge_case_single")
                return AnalysisResult("c", "c_logic_edge_case_single", "edge_case", 0.76, signals)
            if any(k in failed_join for k in ["format", "output", "newline", "spazio", "space"]):
                signals["failed_test_cues"].append("output_format")
                return AnalysisResult("c", "c_output_format", "output_format", 0.75, signals)
            if any(k in failed_join for k in ["bounds", "index", "ultimo", "last", "first"]):
                signals["failed_test_cues"].append("bounds")
                return AnalysisResult("c", "c_logic_bounds_off_by_one", "bounds", 0.79, signals)

        # 4) Code-structure hints when tests fail but messages are generic.
        if signals["score_ratio"] < 0.99:
            feats = signals["code_features"]
            if feats.get("uses_free") and not feats.get("null_check_after_malloc") and feats.get("uses_malloc"):
                return AnalysisResult("c", "c_memory_malloc_no_null_check", "memory", 0.62, signals)
            if feats.get("has_for_loop") and feats.get("uses_array_index"):
                return AnalysisResult("c", "c_logic_loop_bounds_generic", "logic_loop", 0.58, signals)
            return AnalysisResult("c", "c_logic_generic_failed_tests", "logic_generic", 0.45, signals)

        # 5) Full score or no useful signal.
        return AnalysisResult("c", "c_no_hint_needed", "none", 0.2, signals)

    def _safe_ratio(self, score: float, max_score: float) -> float:
        try:
            if max_score <= 0:
                return 0.0
            return max(0.0, min(1.0, float(score) / float(max_score)))
        except Exception:
            return 0.0

    def _extract_code_features(self, code: str) -> dict[str, bool | int]:
        code_l = code.lower()
        # Extremely lightweight and cheap: no AST yet.
        return {
            "uses_malloc": "malloc(" in code_l,
            "uses_free": "free(" in code_l,
            "null_check_after_malloc": bool(re.search(r"if\s*\([^\)]*==\s*null|if\s*\([^\)]*!\s*\w+\)", code_l)),
            "has_for_loop": "for (" in code or "for(" in code,
            "has_while_loop": "while (" in code or "while(" in code,
            "uses_array_index": "[" in code and "]" in code,
            "uses_pointer_deref": "*" in code,
            "uses_address_of": "&" in code,
            "line_count": len(code.splitlines()),
        }
