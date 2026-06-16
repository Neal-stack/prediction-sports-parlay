# Parlay — Improvement Roadmap

Master backlog of work needed before the app is trustworthy for real parlay generation. No timelines — items grouped by area.

---

## Leg compatibility & bettable rules

- Build a single **compatibility engine** used for same-game and multi-game parlays (and eventually props)
- Enforce book-style rules: no both teams to win the same game, no Over + Under same total, no opposite sides (e.g. Spurs ML + Knicks -1.5), no same-team ML + same-team spread
- Extend rules for **correlated legs** across markets (game line + prop interactions when props exist)
- **Pre-save validation** in the UI — block or warn before saving invalid slips
- **Pre-generate validation** — reject invalid combos at generation time, not only on SGP path
- "Book-check passed" indicator on generated parlay cards
- Document book rules as automated test cases for every known bad pattern
- Audit and remove invalid slips from tracker so calibration data isn't poisoned
- Multi-game: keep one leg per game; validate if same-game legs are ever allowed in multi slips

---

## Parlay outcome logic & tracker UX

- Confirm parlay math: **one losing leg = whole parlay loses** (already implemented; make it obvious in UI)
- Show **"Parlay dead"** when first leg loses, with legs settled vs pending
- Label saved slips: SGP / multi-game / valid / invalid
- Duplicate detection: warn when generated slip matches an already-saved slip
- Improve confirm-results workflow messaging for partially settled slips

---

## Prediction model — less odds, more research

- Decouple **model win probability** from **implied probability** — don't anchor picks to the line
- Reduce or remove leg filters based on implied prob bands (`min_leg_implied` / `max_leg_implied`)
- Reduce `price_weight` and `target_implied` influence in leg scoring
- Score legs primarily on **game research**: matchup, form, injuries, pace, rest, H2H, situational factors
- Use odds for **payout calculation and stake math**, not as the main pick driver
- Show per leg: implied %, model %, edge (difference), and top reasons
- Surface "why we disagree with the line" when model diverges from market
- Fix Safe/Balanced/Bold producing identical slips when market pool is tiny (e.g. one NBA game)

---

## Calibration & learning from results

- Feed **confirmed leg outcomes** into calibration (exclude invalid/removed parlays)
- Dashboard: predicted vs actual hit rate by sport, market, risk level
- Auto-adjust confidence when model is systematically over/under market
- Require confirmed results before calibration buckets affect future generation

---

## Player props & bet types

- Fetch **player prop lines** from SharpAPI (or alternate source) — points, rebounds, assists, 3PM, etc.
- DB schema: `player_props` table and/or extend odds storage
- API schema: `market: player_prop`, player, stat, line, side
- Generate prop candidates and score them (minutes, matchup, injury, recent averages)
- Grade prop legs on settlement via box scores (API-Sports)
- SGP compatibility matrix for prop + game line combinations
- Support additional markets per sport as data allows (alt lines, team totals, etc.)

---

## Anchor / boost legs

- Identify **high-confidence legs** (e.g. model prob above threshold) as optional add-ons
- Show **suggested anchor legs** outside the main 5-leg parlay (e.g. "Wembanyama over 20 pts — ~88%")
- Only suggest if not already in the main slip and if book rules allow pairing
- UI section: "Suggested anchors" / optional stack suggestions

---

## AI in parlay generation (not just explanation)

- Move AI from **post-hoc explanation** to **input during candidate selection**
- **Gemini research pass** per game: injuries, narrative, matchups, prop angles → structured JSON signals
- Feed AI signals into leg scoring (not only into chat/summary text)
- **OpenAI** for synthesis, conflict check ("can these legs be parlayed?"), and final rationale
- Chat answers from same research cache; avoid redundant API calls
- Guardrails: compatibility engine + numeric model approve legs; LLM does not alone pick combos
- API budget: AI on shortlist only; optional "lite generate" without AI for preset sweeps

---

## Data pipeline & quality

- Deduplicate games on odds sync (same matchup listed twice)
- Normalize team names for matching and settlement (`BOS Red Sox` vs `Boston Red Sox`)
- Don't score line movement until sufficient snapshot history exists
- Expand context signals: rest days, back-to-backs, season stats, home/away splits, minutes/load
- Sport-specific data: pace (NBA), bullpen (MLB), snap counts (NFL), etc.
- Improve injury matching and depth (who's out matters more than generic team penalty)
- Improve news signal beyond keyword sentiment
- API quota strategy: context cache windows, batch generates, background sync vs on-demand

---

## Generator & selection logic

- Select best parlay by **research-backed win probability and edge**, not combined odds targets alone
- Relax or rethink payout floors driven purely by `min_combined_american` / `max_combined_implied`
- Same-game parlay cap at 3 legs (ML, spread, total — one side each); no 4–5 leg SGP
- Multi-game: uncorrelated legs across distinct games
- Greedy/combo selection must always pass compatibility engine before returning

---

## Frontend & product UX

- Error boundary, consistent error states, retry on failed loads (partially done)
- Edge panel: debounce, abort, unique keys for duplicate markets (partially done)
- Remove button on saved slips (done)
- Don't offer 4–5 legs in same-game mode (done)
- Explain when two risk presets produce the same slip
- Preset runner or batch generate for Safe/Balanced/Bold without redundant AI calls
- Demo mode clearly labeled vs live data

---

## Engineering & ops

- Tests: parlay math, settlement grading, SGP compatibility rules, API smoke tests
- CI on pull requests (partially done)
- Chat rate limiting (done)
- Async Supabase calls / batch odds queries (partial)
- Structured logging, sync health in status endpoint (partial)
- Global exception handling and consistent API errors (partial)
- Migrations documented and applied: tracking, game scores, service role grants

---

## Deployment & configuration

- Production env validation (required keys, no silent demo mode)
- CORS and deploy configs for Vercel + Railway
- README and setup docs kept in sync with migrations and features

---

## Known gaps from initial MVP

- No player bets in any generated parlay
- AI (Gemini/ChatGPT) not used in pick selection — only post-generation insight
- Model win prob ≈ implied prob + small nudge — not independent research
- Invalid same-game combos were possible until compatibility fixes (continue hardening)
- Calibration needs clean, confirmed leg data to improve future picks
