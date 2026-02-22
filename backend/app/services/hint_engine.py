from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from app import db
from app.analyzers.c_adapter import CAdapter
from app.schemas import HintRequest, HintResponse

CATALOG_DIR = Path(__file__).resolve().parent.parent / 'hint_catalog'


class HintEngine:
    def __init__(self) -> None:
        self._c_adapter = CAdapter()
        self._catalog_cache: dict[str, dict[str, Any]] = {}
        # Exploration rate for "learns by trying" hint variants.
        self.epsilon = 0.15

    def handle_hint(self, req: HintRequest) -> HintResponse:
        if req.mode != 'training':
            return HintResponse(enabled=False, hint_text='Hints disabled (exam mode).')

        language = (req.language or 'c').lower()
        if language != 'c':
            # MVP is C-first. We still return a generic hint rather than failing.
            return HintResponse(
                enabled=True,
                hint_level=1,
                hint_type='generic',
                cluster_key=f'{language}_unsupported_mvp',
                hint_text='MVP attivo soprattutto per C. Per questo linguaggio posso dare solo un indizio generico: riparti dal primo test che fallisce e controlla casi limite e formato output.',
                confidence=0.25,
                hint_variant='default'
            )

        analysis = self._c_adapter.analyze(req)
        cluster_key = analysis.cluster_key
        hint_type = analysis.hint_type
        confidence = analysis.confidence

        # Decide hint level based on prior attempts for same student/question context.
        previous = db.get_last_attempt_for_context(
            student_id=req.student_id,
            language=language,
            quiz_id=req.quiz_id,
            question_id=req.question_id,
            question_slot=req.question_slot,
        )

        # Learn from the previous hint effectiveness when current attempt arrives.
        learning_info: dict[str, Any] = {}
        if previous is not None and previous['hint_variant'] and previous['hint_level']:
            prevscore = float(previous['score'] or 0.0)
            currscore = float(req.coderunner.score or 0.0)
            delta = currscore - prevscore
            improved = delta > 1e-9
            db.update_attempt_improvement(int(previous['id']), improved, delta)
            db.bump_hint_stats(
                language=str(previous['language']),
                cluster_key=str(previous['cluster_key'] or 'unknown'),
                hint_level=int(previous['hint_level'] or 1),
                hint_variant=str(previous['hint_variant'] or 'default'),
                exposure_inc=0,
                improvement_inc=1 if improved else 0,
                delta_inc=delta,
            )
            learning_info = {
                'previous_hint_improved_score': improved,
                'previous_delta_score': round(delta, 4)
            }

        hint_level = self._decide_level(previous)
        catalog = self._load_catalog(language)
        hint_variant = self._choose_variant(language, cluster_key, hint_level, catalog)
        hint_text = self._resolve_hint_text(catalog, cluster_key, hint_level, hint_variant)

        # Count exposure of selected hint.
        db.bump_hint_stats(
            language=language,
            cluster_key=cluster_key,
            hint_level=hint_level,
            hint_variant=hint_variant,
            exposure_inc=1,
            improvement_inc=0,
            delta_inc=0.0,
        )

        source_hash = hashlib.sha256((req.source_code or '').encode('utf-8', errors='ignore')).hexdigest()[:16]
        db.insert_attempt({
            'mode': req.mode,
            'language': language,
            'course_id': req.course_id,
            'quiz_id': req.quiz_id,
            'question_id': req.question_id,
            'question_slot': req.question_slot,
            'question_name': req.question_name,
            'student_id': req.student_id,
            'attempt_id': req.attempt_id,
            'attempt_no': req.attempt_no,
            'source_code': req.source_code,
            'source_hash': source_hash,
            'score': req.coderunner.score,
            'max_score': req.coderunner.max_score,
            'compile_error_text': req.coderunner.compile_error_text,
            'runtime_error_text': req.coderunner.runtime_error_text,
            'failed_tests_json': db.json_text(req.coderunner.failed_tests),
            'full_feedback_text': req.coderunner.full_feedback_text,
            'cluster_key': cluster_key,
            'hint_level': hint_level,
            'hint_type': hint_type,
            'hint_variant': hint_variant,
            'hint_text': hint_text,
            'confidence': confidence,
        })

        return HintResponse(
            enabled=True,
            hint_level=hint_level,
            hint_type=hint_type,
            cluster_key=cluster_key,
            hint_text=hint_text,
            confidence=round(confidence, 3),
            hint_variant=hint_variant,
            learning=learning_info or None,
        )

    def _decide_level(self, previous: Any) -> int:
        # Progress only if previous attempt exists and did not already solve everything.
        if previous is None:
            return 1
        prevratio = 0.0
        try:
            if float(previous['max_score'] or 0) > 0:
                prevratio = float(previous['score'] or 0) / float(previous['max_score'] or 1)
        except Exception:
            prevratio = 0.0
        if prevratio >= 0.999:
            return 1
        prevlevel = int(previous['hint_level'] or 1)
        # Simple bounded progression per attempt.
        return min(3, max(1, prevlevel + 1))

    def _load_catalog(self, language: str) -> dict[str, Any]:
        if language in self._catalog_cache:
            return self._catalog_cache[language]
        path = CATALOG_DIR / f'{language}.json'
        if not path.exists():
            self._catalog_cache[language] = {}
            return {}
        self._catalog_cache[language] = json.loads(path.read_text(encoding='utf-8'))
        return self._catalog_cache[language]

    def _choose_variant(self, language: str, cluster_key: str, hint_level: int, catalog: dict[str, Any]) -> str:
        entry = catalog.get(cluster_key) or catalog.get('c_logic_generic_failed_tests') or {}
        variants = list((entry.get('variants') or {}).keys()) or ['default']
        if len(variants) == 1:
            return variants[0]

        if random.random() < self.epsilon:
            return random.choice(variants)

        stats = db.get_hint_stats(language=language, cluster_key=cluster_key, hint_level=hint_level)
        score_by_variant: dict[str, float] = {}
        for row in stats:
            v = str(row['hint_variant'])
            exp = int(row['exposures'] or 0)
            imp = int(row['improvements'] or 0)
            total_delta = float(row['total_delta'] or 0.0)
            # Conservative score = success rate + tiny delta bonus; optimistic for low data via prior.
            rate = (imp + 1.0) / (exp + 2.0)
            score_by_variant[v] = rate + (0.05 * total_delta / max(1.0, exp))

        # Prefer variants with no data occasionally to bootstrap.
        unseen = [v for v in variants if v not in score_by_variant]
        if unseen:
            return random.choice(unseen)

        variants_sorted = sorted(variants, key=lambda v: score_by_variant.get(v, 0.0), reverse=True)
        return variants_sorted[0]

    def _resolve_hint_text(self, catalog: dict[str, Any], cluster_key: str, hint_level: int, hint_variant: str) -> str:
        fallback = catalog.get('c_logic_generic_failed_tests', {})
        entry = catalog.get(cluster_key, fallback)
        variants = entry.get('variants', {})
        chosen = variants.get(hint_variant) or (next(iter(variants.values())) if variants else None)
        if not chosen:
            return 'Controlla il primo test che fallisce e ripercorri il flusso con un input piccolo, concentrandoti su casi limite e gestione della memoria.'
        txt = chosen.get(str(hint_level))
        if txt:
            return txt
        # Graceful fallback to lower levels.
        for level in range(hint_level, 0, -1):
            txt = chosen.get(str(level))
            if txt:
                return txt
        return 'Controlla il primo test che fallisce e ripercorri il flusso con un input piccolo, concentrandoti su casi limite e gestione della memoria.'
