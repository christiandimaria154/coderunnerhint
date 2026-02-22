# CodeRunner Hint Engine (C-first MVP)

FastAPI backend that receives code + CodeRunner feedback and returns a progressive hint.

## Features (MVP)
- C-first analyzer with didactic categories (compile/runtime/logic)
- Progressive hints (level 1 → 2 → 3)
- Lightweight self-learning on hint variants (tracks which variants improve score more often)
- SQLite persistence (`backend/data/hint_engine.sqlite3`)

## Run
```bash
cd backend
./run.sh
```

Optional API key (recommended in production):
```bash
export HINT_ENGINE_API_KEY="your-secret"
./run.sh
```

## Endpoint
- `GET /health`
- `POST /hint`
- `GET /stats/top`

## Example request
See `samples_hint_request_c.json` in the project root.

## Notes
- This MVP is intentionally heuristic-based (regex + cheap code features).
- It ignores most trivial syntax-only errors and focuses on didactically useful hints.
- To improve segfault diagnostics, configure CodeRunner tests with runtime sanitizer feedback where possible.
