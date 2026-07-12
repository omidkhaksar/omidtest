# Deploy on Vercel — full guide

Complete path to deploy **House Hunt Tracker** (dashboard + API + Telegram bot) on Vercel.

---

## What runs on Vercel

| Component | On Vercel? | How |
|-----------|------------|-----|
| Dashboard (kanban UI) | ✅ | Served by FastAPI |
| REST API | ✅ | FastAPI in `Dockerfile.vercel` |
| Telegram bot | ✅ | Webhook at `/api/telegram/webhook` |
| Database | ⚠️ | **Must use Postgres** (SQLite is ephemeral) |

---

## Prerequisites

1. [GitHub](https://github.com) account
2. [Vercel](https://vercel.com) account
3. [Neon](https://neon.tech) Postgres (free tier) — via Vercel Marketplace
4. Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Step 1 — Push code to GitHub

```bash
cd omidtest
git init
git add .
git commit -m "Prepare Vercel deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USER/house-hunt.git
git push -u origin main
```

---

## Step 2 — Create Postgres database (Neon)

1. Open [Vercel Dashboard](https://vercel.com/dashboard) → **Storage** → **Create Database**
2. Choose **Neon** (Postgres)
3. Create database and link it to your project
4. Vercel adds `POSTGRES_URL` automatically

> Do **not** use SQLite on Vercel — data is lost when the container scales down.

---

## Step 3 — Import project on Vercel

1. Vercel Dashboard → **Add New** → **Project**
2. Import your GitHub repo
3. Vercel detects `Dockerfile.vercel` automatically (preset: **Container**)
4. **Do not** change the framework preset — container is auto-detected

---

## Step 4 — Environment variables

In **Project → Settings → Environment Variables**, add:

| Variable | Value | Required |
|----------|-------|----------|
| `DATABASE_URL` | Copy from Neon (`POSTGRES_URL`) | ✅ |
| `DASHBOARD_URL` | `https://YOUR-PROJECT.vercel.app` | ✅ |
| `TELEGRAM_BOT_TOKEN` | From @BotFather | ✅ |
| `TELEGRAM_WEBHOOK_SECRET` | Random string (e.g. `openssl rand -hex 32`) | ✅ recommended |
| `TELEGRAM_ALLOWED_USER_IDS` | Your Telegram user id | optional |
| `RUN_MODE` | `api` | auto-set |
| `TELEGRAM_MODE` | `webhook` | auto-set |

**Example:**

```env
DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require
DASHBOARD_URL=https://house-hunt.vercel.app
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
TELEGRAM_WEBHOOK_SECRET=a1b2c3d4e5f6...
TELEGRAM_ALLOWED_USER_IDS=12345678
```

Apply to **Production**, **Preview**, and **Development**.

---

## Step 5 — Deploy

### Option A — Git push (recommended)

```bash
git push origin main
```

Vercel deploys automatically on every push to `main`.

### Option B — Vercel CLI

```bash
npm i -g vercel
vercel login
vercel link
vercel deploy --prod
```

Or use the helper script:

```bash
chmod +x scripts/deploy-vercel.sh
./scripts/deploy-vercel.sh
```

---

## Step 6 — Verify deployment

1. **Health check**

   ```
   https://YOUR-PROJECT.vercel.app/api/health
   ```

   Expected:

   ```json
   {
     "ok": true,
     "telegram_webhook": true,
     "database": "postgres"
   }
   ```

2. **Dashboard**

   Open `https://YOUR-PROJECT.vercel.app`

3. **Telegram bot**

   - Send `/start` to your bot
   - Paste a Divar link → should save to the board
   - Check the dashboard — card appears in **جدید**

---

## Step 7 — Update bot menu (optional)

In @BotFather:

```
/setcommands
```

```
start - شروع
help - راهنما
board - باز کردن برد
list - لیست آگهی‌ها
call - تماس بگیر
reject - رد کردن
accept - در حال پیگیری
bought - خریدم
wait - لیست انتظار
noanswer - جواب نداد
remove - حذف
note - یادداشت
```

---

## Architecture on Vercel

```
Telegram  ──webhook──▶  Vercel Container (Dockerfile.vercel)
                              │
                              ├── FastAPI :80
                              ├── /api/*
                              ├── /static/*
                              └── /api/telegram/webhook
                              │
                              ▼
                         Neon Postgres
```

On startup, the app registers the webhook:

```
https://YOUR-PROJECT.vercel.app/api/telegram/webhook
```

---

## Local test before deploy

Test the Vercel container image locally:

```bash
# Requires Docker running
vercel dev
```

Or build manually:

```bash
docker build -f Dockerfile.vercel -t house-hunt-vercel .
docker run --rm -p 8080:80 \
  -e DATABASE_URL="postgresql://..." \
  -e DASHBOARD_URL="http://localhost:8080" \
  -e TELEGRAM_BOT_TOKEN="..." \
  -e TELEGRAM_MODE="webhook" \
  house-hunt-vercel
```

---

## Troubleshooting

### `telegram_webhook: false` in /api/health

- Check `TELEGRAM_BOT_TOKEN` is set
- Check `TELEGRAM_MODE=webhook` (or `VERCEL` env is present)
- Check deploy logs for webhook registration errors

### Bot does not respond

- `DASHBOARD_URL` must match your production URL exactly (https, no trailing path)
- Redeploy after changing `DASHBOARD_URL`
- Verify webhook: `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`

### Data disappears

- You are using SQLite — switch to `DATABASE_URL` with Postgres

### 403 on webhook

- `TELEGRAM_WEBHOOK_SECRET` must match between Vercel env and what was sent to Telegram on `setWebhook`

### Build fails

- Ensure `Dockerfile.vercel` is at project root
- Check Vercel plan supports Container Images

---

## Redeploy after env changes

```bash
vercel deploy --prod
```

Or push any commit to `main`.

---

## Cost notes

- Vercel container functions use **Active CPU** pricing
- Neon free tier: 0.5 GB storage, sufficient for personal use
- Container scales to zero after ~5 min idle (cold start on next request)

---

## Files reference

| File | Role |
|------|------|
| `Dockerfile.vercel` | Container image for Vercel |
| `vercel.json` | Sets `RUN_MODE=api`, `TELEGRAM_MODE=webhook` |
| `.vercelignore` | Excludes dev files from upload |
| `app/telegram_webhook.py` | Webhook handler for Telegram |
| `scripts/deploy-vercel.sh` | One-command production deploy |
