# Parlay — Prediction Market MVP

Generate uncorrelated parlays from live odds, line movement, injuries, weather, and news. Risk-aware slips with edge analysis, bankroll tracking, and optional AI chat.

## Stack

| Layer | Tech |
|-------|------|
| Frontend | Next.js 16, Tailwind 4 |
| Backend | FastAPI + APScheduler |
| DB | Supabase |
| Odds | SharpAPI |
| Injuries | API-Sports |
| News | GNews |
| Weather | Open-Meteo (no key) |
| AI | OpenAI or Gemini (optional) |

## Quick start

```bash
# One-time setup
cd backend && cp .env.example .env && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd ../frontend && npm install && cp .env.local.example .env.local
cd .. && npm install

# Run both apps from repo root
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

**API keys go in `backend/.env` only.** Frontend only needs `NEXT_PUBLIC_API_URL=http://localhost:8000`.

---

## API keys — step by step

### 1. Supabase (required for live data)

1. Go to [supabase.com/dashboard](https://supabase.com/dashboard)
2. Open your project (or create one)
3. **SQL Editor** → paste and run `supabase/migrations/001_initial.sql`
4. **Settings → API** (left sidebar)
5. Copy these into `backend/.env`:
   - **Project URL** → `SUPABASE_URL`
   - **service_role** key (under "Project API keys") → `SUPABASE_SERVICE_KEY`

> Use the **service_role** key, not the `anon` key. The backend needs write access for odds snapshots.

### 2. SharpAPI (required for live odds)

1. Sign up at [sharpapi.io](https://sharpapi.io)
2. Dashboard → API Keys
3. Copy → `SHARPAPI_KEY` in `backend/.env`

### 3. API-Sports (recommended — injuries)

1. Register at [dashboard.api-football.com/register](https://dashboard.api-football.com/register)
2. **Account → My Access** → copy API key
3. Paste → `API_SPORTS_KEY`

### 4. GNews (recommended — team news)

1. Sign up at [gnews.io](https://gnews.io)
2. Dashboard → API key
3. Paste → `GNEWS_API_KEY`

### 5. AI chat (optional — pick one)

**ChatGPT Plus ($20/mo) does NOT include API access.** It's a separate product for chatting at chatgpt.com. For this app you need a developer API key:

**Option A — OpenAI (recommended)**
1. Go to [platform.openai.com](https://platform.openai.com)
2. Create account → **API keys** → Create new key
3. Add billing (pay-per-use; `gpt-4o-mini` is ~$0.15/1M input tokens)
4. Paste → `OPENAI_API_KEY` in `backend/.env`

**Option B — Gemini (free tier)**
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Create key → `GEMINI_API_KEY`

**Dual AI mode** (when both keys are set):
- **Parlay insight:** Gemini extracts signals (free) → OpenAI writes the summary (small/cheap call)
- **Simple chat:** Gemini only (free, saves OpenAI usage)
- **Complex chat** (risk, strategy, compare): Gemini signals → OpenAI answer

> OpenAI API is pay-per-use (~pennies with `gpt-4o-mini`), not included in ChatGPT Plus.

### 6. Weather

No key needed — uses [Open-Meteo](https://open-meteo.com).

---

## Features

| Feature | Description |
|---------|-------------|
| Risk levels | Safe / Balanced / Bold — optimizes win prob vs payout |
| Games board | Today's slate with odds; tap a game for line movement chart |
| Edge panel | Sliders to set your win % per leg vs implied odds |
| Bankroll tracker | Local tracker with 5% max stake guardrail |
| AI analyst | Explains parlays + chat (OpenAI or Gemini) |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/status` | Active integrations |
| `GET /api/games` | Today's games |
| `GET /api/games/{id}/line-movement` | Odds history for chart |
| `POST /api/parlay/generate` | `{ "legs": 3, "risk": "balanced" }` |
| `POST /api/parlay/analyze-edge` | User probability vs implied |
| `POST /api/chat` | AI analyst |

## Deploy (when ready)

Configs are included — no deploy needed until you're ready.

| App | Platform | Root dir | Key env |
|-----|----------|----------|---------|
| Frontend | Vercel | `frontend/` | `NEXT_PUBLIC_API_URL` |
| Backend | Railway | `backend/` | All keys from `.env.example` |
| DB | Supabase | — | Already hosted |

After Vercel deploy, set `CORS_ORIGINS=https://your-app.vercel.app` on Railway.

## Project structure

```
prediction-parlay/
├── frontend/          # Next.js UI
├── backend/           # FastAPI engine
├── supabase/          # SQL migrations
├── package.json       # npm run dev (both apps)
└── README.md
```
