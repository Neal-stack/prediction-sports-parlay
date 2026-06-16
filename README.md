# Parlay — Prediction Market MVP

Generate parlays from an **independent win-probability model** (team power ratings, injuries, rest, weather) and compare it to the betting line to find edge. Odds are used only for payout math — never to drive the pick. Risk-aware slips, player-prop anchors, edge analysis, bankroll tracking, and AI research/chat.

**Runs 100% free with zero keys** — ESPN (odds, scores, injuries, news, player stats) and Open-Meteo (weather) need no key. Add a free Gemini key to sharpen picks with the AI research pass. That's the only key worth adding.

## Stack

| Layer | Tech | Key? |
|-------|------|------|
| Frontend | Next.js 16, Tailwind 4 | — |
| Backend | FastAPI + APScheduler | — |
| DB | Supabase | optional |
| Odds + scores + injuries + news | **ESPN** (free, no key) | none |
| Player stats + box scores (NBA props) | **ESPN** (free, no key) | none |
| Weather | Open-Meteo | none |
| AI research + chat | **Gemini 2.5 Flash-Lite** (free) | free key |
| Richer multi-book odds | The Odds API (optional) | free 500/mo |

### How the model works

1. **Independent base probability** — each team's power rating comes from season scoring margin + win rate (ESPN standings), adjusted for home edge, **rest / back-to-backs** (a team on a B2B is penalized), and *player-weighted* injuries (a starting QB out hurts far more than a backup). Converted to win probability with a per-sport logistic. This number does **not** look at the line.
2. **Gemini research pass** — before picks are made, Gemini reads injuries/news and returns a small, bounded structured signal (±8% max) plus prop angles. Cached per game; chat reuses the same cache. If Gemini is offline or rate-limited, the model runs without it.
3. **Edge** — model probability minus the market's implied probability. Legs are selected by edge + risk-weighted win probability, not by odds bands.
4. **Player-prop anchors** — NBA props projected from real ESPN season averages, surfaced as optional add-ons. Angles come from the Gemini research pass, with an ESPN season-leaders fallback when Gemini is unavailable (so props work with no AI key). Free sources don't expose live prop *lines*, so lines are model-derived and labeled as such. Added props are graded automatically from ESPN box scores at settlement.

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

**None of these are required.** With an empty `.env` the app uses ESPN + Open-Meteo (all free, no key) and serves real games, odds, scores, the independent model, **and NBA player-prop anchors** (props fall back to ESPN season leaders when there's no AI key). Add Gemini to make the picks smarter.

### 1. Gemini — AI research pass (free, the one key worth adding)

This is what makes the picks smart: Gemini reads injuries/news pre-generation and feeds a structured signal into the model, and powers the chat analyst.

1. Go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. **Create API key** → "Create API key in new project" (avoids a `limit: 0` quota trap)
3. Paste → `GEMINI_API_KEY` in `backend/.env`

> The free tier lives on the current-gen model. Keep `GEMINI_MODEL=gemini-2.5-flash-lite` (the default) — older `gemini-2.0-flash` returns `limit: 0` on free keys. The key looks like `AQ.…` or `AIza…` depending on when it was issued; both work.

### 2. Supabase — tracking + line history (optional)

Without it, the parlay tracker still works via your browser's localStorage; you just lose cross-device sync, line-movement history, and model calibration.

1. Go to [supabase.com/dashboard](https://supabase.com/dashboard) → open/create a project
2. **SQL Editor** → run migrations **in order**:
   - `supabase/migrations/001_initial.sql`
   - `supabase/migrations/002_service_role_grants.sql`
   - `supabase/migrations/003_parlay_tracking.sql`
   - `supabase/migrations/004_tracking_grants.sql`
   - `supabase/migrations/005_game_scores.sql`
   - `supabase/migrations/006_player_props.sql`
3. **Settings → API** → copy into `backend/.env`:
   - **Project URL** → `SUPABASE_URL`
   - **service_role** key → `SUPABASE_SERVICE_KEY`

> Use the **service_role** key, not the `anon` key — the backend needs write access.

### 3. The Odds API — richer multi-book lines (optional)

ESPN odds are the free default and are plenty for most use. Only add this for sharper multi-book numbers. Free tier is **500 credits/month**, so keep `ODDS_SYNC_MINUTES` ≥ 30.

1. Get a key at [the-odds-api.com](https://the-odds-api.com/)
2. Paste → `ODDS_API_KEY`

### 4. OpenAI — optional paid AI fallback

Not needed — Gemini covers AI for free. If you already have an OpenAI key, set `OPENAI_API_KEY` and it's used only when Gemini is unavailable. (ChatGPT Plus is **not** API access.)

---

## Features

| Feature | Description |
|---------|-------------|
| Independent model | Win probability from team power ratings + injuries + rest, not the line |
| Per-leg edge | Each leg shows implied %, model %, and edge; flags where the model beats the market |
| Risk levels | Safe / Balanced / Bold — re-weight win probability vs edge |
| Player-prop anchors | NBA props projected from season averages, surfaced as optional add-ons |
| Book-check | Generated slips are validated against sportsbook conflict rules |
| Games board | Today's slate with odds; tap a game for line movement chart |
| Edge panel | Sliders to set your win % per leg vs implied odds |
| Parlay tracker | Save slips, mark leg results, bankroll with 5% max stake |
| Model calibration | Confirmed leg results tune future win-probability estimates |
| AI analyst | Pre-generation research pass + chat (Gemini, OpenAI fallback) |

## Track results & improve the model

1. **Generate** a parlay. Optionally add a **Suggested anchor** (a player prop) to the slip with **+ Add** — it recomputes combined odds and joins the legs.
2. Click **Save parlay** in the tracker (set your stake).
3. **Wait** for games to finish.
4. **Return** and expand the saved slip — tap **Check results** to auto-grade. Game lines grade from final scores; player props grade from ESPN box scores. Then **Confirm all** (or override individual legs).
5. The app records outcomes and **calibrates** future picks (needs Supabase).

Results sync to Supabase when configured; they also persist in your browser via localStorage.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/status` | Active integrations + sync health |
| `GET /api/games` | Today's games |
| `GET /api/games/{id}/line-movement` | Odds history for chart |
| `POST /api/parlay/generate` | `{ "legs": 3, "risk": "balanced" }` |
| `POST /api/parlay/analyze-edge` | User probability vs implied |
| `POST /api/chat` | AI analyst (rate limited) |
| `POST /api/tracking/parlays` | Save a parlay slip |
| `GET /api/tracking/parlays` | List saved slips (session header) |
| `PATCH /api/tracking/parlays/{id}/legs` | Record leg result |
| `POST /api/tracking/suggest` | Auto-grade pending legs from final scores |
| `POST /api/tracking/parlays/{id}/confirm` | Confirm suggested results |
| `GET /api/tracking/performance` | Win/loss stats + calibration gap |

## Deploy

Live:

- **App:** [prediction-sports-parlay.vercel.app](https://prediction-sports-parlay.vercel.app)
- **API:** [prediction-parlay-api.onrender.com](https://prediction-parlay-api.onrender.com)

| App | Platform | Root dir | Notes |
|-----|----------|----------|-------|
| Frontend | Vercel | `frontend/` | Env: `NEXT_PUBLIC_API_URL` = the Render API URL |
| Backend | Render (free) | `backend/` | Defined by `render.yaml` blueprint at repo root |
| DB | Supabase | — | Already hosted; run all migrations |

### Backend → Render

1. [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → connect this repo. Render reads `render.yaml` and creates the `prediction-parlay-api` web service (Python, free plan, root dir `backend`).
2. Fill the secret env vars when prompted: `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY` (optional), `CORS_ORIGINS`. The rest are baked into `render.yaml`.
3. Verify `https://<service>.onrender.com/health` returns `{"status":"ok"}`.

### Frontend → Vercel

1. [vercel.com/new](https://vercel.com/new) → import this repo.
2. **Root Directory = `frontend`** (monorepo — preset auto-detects Next.js).
3. Env var `NEXT_PUBLIC_API_URL` = the Render API URL (no trailing slash; leave it non-sensitive).
4. Deploy.

### Wire CORS (required)

On Render → `prediction-parlay-api` → **Environment**, set `CORS_ORIGINS` to your Vercel URL (keep localhost for dev):

```
https://prediction-sports-parlay.vercel.app,http://localhost:3000
```

Without this the live site shows "Failed to fetch" (browser origin blocked).

> **Free-tier note:** the Render service spins down after ~15 min idle; the first request then takes ~30–50s to wake, and the background odds scheduler pauses while asleep. The board still loads live data via on-demand ESPN fetches.

## Project structure

```
prediction-parlay/
├── frontend/          # Next.js UI
├── backend/           # FastAPI engine
├── supabase/          # SQL migrations
├── package.json       # npm run dev (both apps)
└── README.md
```
