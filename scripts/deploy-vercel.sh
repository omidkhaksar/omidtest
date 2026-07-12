#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v vercel >/dev/null 2>&1; then
  echo "Installing Vercel CLI..."
  npm install -g vercel
fi

echo "==> Deploying to Vercel (production)"
vercel deploy --prod

echo ""
echo "Done. Next steps:"
echo "  1. Set env vars in Vercel dashboard (see DEPLOY_VERCEL.md)"
echo "  2. Redeploy after adding DATABASE_URL and TELEGRAM_BOT_TOKEN"
echo "  3. Open /api/health to verify"
