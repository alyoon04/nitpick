# Nitpick

AI code reviewer that learns your repo's taste and argues back.

Most code review bots apply generic best practices. Nitpick reads your merged PR history — which comments were addressed vs. dismissed, what patterns kept showing up — and builds a taste profile for your specific repo. It stops flagging things your team consistently ignores and gets sharper on things you consistently catch.

When a developer disagrees with a comment, Nitpick doesn't just disappear. It re-reads the context, considers their argument, and either concedes or pushes back with a counter-argument.

## How It Works

1. **PR opened** — Nitpick fetches the diff, loads your repo's taste rules, and sends it to Claude for review. Comments are posted as a single GitHub review.
2. **Developer replies** — If someone pushes back on a comment, Nitpick evaluates their argument against the code context and repo history, then concedes or pushes back with reasoning.
3. **PR merged** — Nitpick analyzes which of its comments were addressed (code changed) vs. dismissed (merged as-is) and adjusts taste rule weights accordingly.

## Features

- **Taste learning** — Per-repo review preferences that improve with every merged PR
- **Debate agent** — Real back-and-forth on review comments, not fire-and-forget
- **Structured reviews** — Comments include severity (critical/warning/suggestion) and category (security/bug/error-handling/performance/design)
- **Smart filtering** — Skips lockfiles, generated code, and files over 500 lines

## Setup

### Prerequisites

- Python 3.11+
- Docker (for Postgres + Redis)
- A [GitHub App](https://docs.github.com/en/apps/creating-github-apps)
- An [Anthropic API key](https://console.anthropic.com/settings/keys)

### 1. Clone and install

```bash
git clone https://github.com/alyoon04/nitpick.git
cd nitpick
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```
GITHUB_APP_ID=<your app id>
GITHUB_PRIVATE_KEY_PATH=./github-private-key.pem
GITHUB_WEBHOOK_SECRET=<your webhook secret>
ANTHROPIC_API_KEY=<your api key>
```

### 3. Create a GitHub App

1. Go to [GitHub App settings](https://github.com/settings/apps/new)
2. Set **Webhook URL** to your public URL + `/webhooks/github` (use [ngrok](https://ngrok.com) for local dev)
3. **Repository permissions**: Contents (read), Pull requests (read & write)
4. **Subscribe to events**: Pull request, Pull request review comment
5. Generate and download a private key (`.pem` file)
6. Install the app on your target repos

### 4. Start services

```bash
# Start Postgres and Redis
docker compose up -d db redis

# Run database migrations
alembic upgrade head

# Start the API server
PYTHONPATH=src uvicorn nitpick.api.app:app --reload

# Start the worker (separate terminal)
PYTHONPATH=src arq nitpick.workers.settings.WorkerSettings
```

### 5. Expose locally with ngrok

```bash
ngrok http 8000
```

Copy the ngrok URL into your GitHub App's webhook settings.

## Architecture

```
GitHub Webhooks
       │
   FastAPI (async)
       │
   ┌───┴───┐
   │       │
  Arq    Direct
 Queue   Response
   │
   ├── review_task  → fetch diff → Claude → post review
   ├── reply_task   → load thread → Claude debate → post reply
   └── ingest_task  → analyze outcomes → update taste rules
       │
   Postgres + pgvector
```

## Stack

| Layer | Choice |
|-------|--------|
| API | FastAPI (async) |
| Task queue | arq + Redis |
| Database | Postgres + pgvector |
| LLM | Claude (Anthropic) |
| GitHub API | httpx (async) |

## Project Structure

```
src/nitpick/
├── api/
│   ├── app.py              # FastAPI app
│   └── webhooks.py         # GitHub webhook handlers
├── db/
│   └── session.py          # Async SQLAlchemy engine
├── models/
│   └── tables.py           # Database models
├── services/
│   ├── github.py           # GitHub API client
│   ├── reviewer.py         # Claude review + debate logic
│   └── diff_parser.py      # Unified diff parser
├── workers/
│   ├── settings.py         # Arq worker config
│   ├── review.py           # PR review task
│   ├── reply.py            # Comment reply task
│   └── ingest.py           # PR merge ingest task
└── config.py               # App settings
```

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/

# Format
ruff format src/
```

## License

MIT
