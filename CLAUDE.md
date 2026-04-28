# Nitpick — AI Code Reviewer

AI-powered GitHub App that reviews PRs, learns your repo's review taste over time, and argues back when developers disagree with its comments.

## Core Concepts

- **Taste Profile** — per-repo learned preferences built from merged PR history. Stored as structured rules (taste_rules table) + semantic embeddings (pgvector). Not a fine-tuned model — it's a signal layer that conditions the LLM prompt.
- **Debate Agent** — when a developer replies disagreeing with a bot comment, the agent re-reads context, checks repo history, and either concedes or pushes back with reasoning.

## Architecture

```
GitHub Webhooks (PR opened/updated, comment reply, PR merged)
       │
   FastAPI (async)
       │
   ┌───┴───┐
   │       │
  Arq    Direct
 Queue   Response
   │
   ├── review_task  → fetch diff → Claude review → post GitHub review
   ├── reply_task   → load thread → Claude debate → post reply
   └── ingest_task  → analyze outcomes → update taste_rules + embeddings
       │
   Postgres + pgvector
```

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| API | FastAPI (async) | Webhook server, concurrent requests |
| Task queue | arq (async + Redis) | Lightweight, async-native, replaces Celery |
| DB | Postgres + pgvector + async SQLAlchemy + asyncpg | One DB, async throughout |
| GitHub API | httpx (async) | Direct REST calls, no sync bottleneck |
| LLM | Claude via anthropic SDK | Tool use, long context for diffs |
| Embeddings | Deferred (Voyage later) | Start with keyword matching + structured rules |
| Local dev | Docker Compose (Postgres + Redis only) | App runs on host |
| Production deploy | Fly.io or Railway | Managed, scalable |

## Key Stack Decisions

1. **arq over Celery** — Celery is sync, heavy, hard to debug. arq is async-native, uses Redis, minimal config. Our workers just call APIs and post results.
2. **httpx over PyGithub** — PyGithub is sync/blocking. We only need ~5 GitHub API endpoints. httpx is async and already a dependency.
3. **Async throughout** — FastAPI is async, DB is async (asyncpg), HTTP calls are async (httpx), workers are async (arq). No sync bottlenecks.
4. **Embeddings deferred** — Taste rules work with structured data first. Add pgvector semantic search when there's enough historical data to make it useful.
5. **Sync Alembic** — Alembic migrations use a separate sync psycopg connection. This is standard — migrations don't need async.

## Database Schema

Tables: `repos`, `pull_requests`, `review_comments`, `comment_threads`, `taste_rules`, `review_embeddings`

See `src/nitpick/models/tables.py` for full schema.

## Project Layout

```
src/nitpick/
├── api/
│   ├── app.py           # FastAPI app + health endpoint
│   └── webhooks.py      # GitHub webhook endpoints
├── db/
│   ├── session.py        # async engine + session factory
│   └── __init__.py
├── models/
│   ├── tables.py         # SQLAlchemy models
│   └── __init__.py
├── services/
│   ├── github.py         # GitHub API client (httpx, JWT auth)
│   ├── reviewer.py       # Claude review logic + prompt building
│   └── diff_parser.py    # Unified diff → structured chunks
├── workers/
│   ├── settings.py       # arq WorkerSettings
│   ├── review.py         # review_task
│   ├── reply.py          # reply_task
│   └── ingest.py         # ingest_task
└── config.py             # pydantic-settings
```

## Three Workers

### review_task (PR opened/updated)
1. Fetch diff via GitHub API
2. Parse diff → file chunks (skip lockfiles, generated files, >500 line files)
3. For each chunk: build prompt with diff + taste_rules + similar past comments
4. Call Claude → structured review comments
5. Post as single GitHub Review via Reviews API
6. Store comments in DB

### reply_task (comment reply to bot)
1. Load thread history from comment_threads
2. Build debate prompt: original diff + our comment + their reply + taste rules
3. Claude decides: concede or push_back with reasoning
4. Post reply, update thread, adjust taste_rule weight if conceded

### ingest_task (PR merged)
1. Fetch all review comments on merged PR
2. For each bot comment: was it addressed (code changed) or dismissed (merged as-is)?
3. Update taste_rules: addressed → increase weight, dismissed → decrease weight

## Taste Profile System

Explicit rules injected into the review prompt:
```
REPO TASTE RULES:
- Always flag: auth middleware missing (flagged 12x, addressed 11x, weight: 0.95)
- Ignore: single-letter vars (flagged 4x, dismissed 4x, weight: -0.6)
```

Weight range: -1.0 (never flag) to +1.0 (always flag). Rules with |weight| < 0.2 are omitted from prompts.

## Build Phases

### Phase 1 — Infrastructure (current)
- [x] Docker Compose (Postgres + pgvector, Redis)
- [ ] pyproject.toml with revised deps
- [ ] Async DB layer + models
- [ ] Alembic migrations
- [ ] Arq worker setup

### Phase 2 — Vertical Slice
- [ ] GitHub service (JWT auth, fetch diff, post review)
- [ ] Webhook endpoint + signature verification
- [ ] Diff parser
- [ ] Review service + Claude integration
- [ ] End-to-end: PR opened → review posted

### Phase 3 — Taste Learning
- [ ] Ingest worker (analyze merged PR outcomes)
- [ ] Taste rules in review prompt
- [ ] Weight adjustment loop

### Phase 4 — Debate Agent
- [ ] Reply worker (detect reply to bot comment)
- [ ] Debate prompt + concede/push_back
- [ ] Thread tracking

## Commands

```bash
# Local dev
docker compose up -d db redis          # start Postgres + Redis
alembic upgrade head                    # run migrations
uvicorn nitpick.api.app:app --reload    # start API
arq nitpick.workers.settings.WorkerSettings  # start worker

# Full stack (Docker)
docker compose up --build
```

## Environment Variables

See `.env.example` — requires GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET, GITHUB_PRIVATE_KEY_PATH, ANTHROPIC_API_KEY.
