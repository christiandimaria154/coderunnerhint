from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.schemas import HintRequest, HintResponse
from app.services.hint_engine import HintEngine

app = FastAPI(title='CodeRunner Hint Engine', version='0.1.0-alpha')
engine = HintEngine()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # Restrict in production.
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
def _startup() -> None:
    db.init_db()


def check_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv('HINT_ENGINE_API_KEY', '').strip()
    if expected and (x_api_key or '').strip() != expected:
        raise HTTPException(status_code=401, detail='Invalid API key')


@app.get('/health')
def health() -> dict:
    return {'status': 'ok', 'service': 'coderunner-hint-engine', 'version': app.version}


@app.post('/hint', response_model=HintResponse, dependencies=[Depends(check_api_key)])
def hint(req: HintRequest) -> HintResponse:
    return engine.handle_hint(req)


@app.get('/stats/top', dependencies=[Depends(check_api_key)])
def stats_top(limit: int = 20) -> dict:
    # Lightweight admin endpoint for quick debugging.
    with db.get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT language, cluster_key, hint_level, hint_variant, exposures, improvements, total_delta,
                   ROUND(CAST(improvements AS REAL) / CASE WHEN exposures = 0 THEN 1 ELSE exposures END, 3) AS improve_rate
            FROM hint_stats
            ORDER BY exposures DESC, improvements DESC
            LIMIT ?
            ''',
            (max(1, min(limit, 200)),)
        ).fetchall()
    return {'items': [dict(r) for r in rows]}
