# House Hunt Tracker

Save Divar links from Telegram, then follow each house on a kanban board.

## Flow

1. Send a Divar link to your Telegram bot
2. Bot saves it to the board (column: **New**)
3. Open the dashboard and drag cards:
   - **Need Call** — you must call the host
   - **No Answer** — called, no answer
   - **Waitlist** — keep for later
   - **In Talk** — host answered, visit/negotiation
   - **Rejected** — not interested
   - **Bought** — you got it

## Setup

```bash
cd omidtest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...   # from @BotFather
TELEGRAM_ALLOWED_USER_IDS=12345678 # optional, your Telegram user id
DASHBOARD_URL=http://localhost:8000
```

### Run everything (API + bot)

```bash
python run.py
```

### Run API only

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Run bot only (API must already be running)

```bash
python -m app.bot
```

## Use

- **Telegram:** paste a link like `https://divar.ir/v/...`
- **Dashboard:** http://localhost:8000
- Drag cards between columns or click **Edit**
- Add notes: phone number, visit time, why you rejected

## API

- `GET /api/kanban` — board data
- `POST /api/listings` — `{ "url": "..." }`
- `POST /api/listings/{id}/move` — `{ "status": "need_call" }`
- `PATCH /api/listings/{id}` — update title, notes, status

## Telegram commands

**Save:** send a Divar link

**Change status:**
- `/call <link|id>` — need to call
- `/reject <link|id>` — rejected
- `/accept <link|id>` — in talk
- `/bought <link|id>` — purchased
- `/wait <link|id>` — waitlist
- `/noanswer <link|id>` — no answer
- `/remove <link|id>` — delete

**Or type:** `reject https://divar.ir/v/abc` or `call 3`

**Reply** to a saved ad message with: `reject`, `call`, `accept`, `bought`, `wait`, `remove`

**Other:** `/note <link|id> text`, `/list`, `/board`, `/help`

Buttons under each saved ad also update the board instantly.

## Docker (local / VPS)

API only:

```bash
docker compose up --build api
```

API + Telegram bot:

```bash
docker compose --profile bot up --build
```

Dashboard: http://localhost:8000

Data is stored in a Docker volume (`house_hunt_data`). Set env vars in `.env` before starting.

## Deploy on Vercel

Full step-by-step guide: **[DEPLOY_VERCEL.md](./DEPLOY_VERCEL.md)**

1. Push repo to GitHub
2. Add **Neon Postgres** in Vercel Storage
3. Import project (Vercel detects `Dockerfile.vercel`)
4. Set env: `DATABASE_URL`, `DASHBOARD_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`
5. Deploy → `https://your-app.vercel.app`

Telegram bot runs via **webhook** on Vercel (no separate bot server).

```bash
./scripts/deploy-vercel.sh   # or: vercel deploy --prod
```

## Stack

- FastAPI + SQLite / Postgres
- python-telegram-bot (polling locally, webhook on Vercel)
- Simple HTML kanban (drag & drop)
