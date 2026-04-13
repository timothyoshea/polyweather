# PolyWeather

## On Session Start
- Read `code_explainer.md` in the repo root — complete map of all pages, API routes, DB tables, and app structure
- Read the project history from memory (`project_history.md`) to understand what's been built and the current state
- Check `.claude/settings.local.json` for hooks and permissions already configured
- Auto-commit hook is set up: every Edit/Write auto-commits with "Auto-commit: update <filename>"
- Vercel deploy status hook runs after every `git push`
- After making structural changes (adding/removing pages, API routes, DB tables, or major features), update `code_explainer.md`

## Project Context
Weather temperature prediction trading system for Polymarket. Scans markets, forecasts with multi-model weather data, paper trades automatically, tracks P&L across multiple portfolios.

## Key Conventions
- **Frontend:** Vanilla JS/HTML in `public/` directory (no frameworks). Dark theme with CSS variables. Chart.js for charts.
- **Backend:** Python Vercel serverless functions in `api/`. Uses `urllib.request` (not `requests` library). `BaseHTTPRequestHandler` pattern.
- **Database:** Supabase (Postgres) with REST API. Service key for writes, anon key for reads.
- **Deployments:** Push to `main` auto-deploys to Vercel (`polyweather-nine.vercel.app`). Railway trader deploys via `railway up` from the `railway-trader/` subdirectory (NOT repo root).
- **Portfolio system:** Multiple portfolios with separate strategies. "Datagrab" = unlimited capital (captures all data). Fixed-capital portfolios enforce position sizing.
- **Temperatures:** Always display in the market's native unit. If Polymarket shows Fahrenheit (US cities), show Fahrenheit in our UI. If Celsius, show Celsius. Never convert — match Polymarket exactly.

## Working Preferences
- Use agent teams for parallel work across multiple files
- Auto-commit after every change (hook configured)
- Push to git frequently
- Don't ask for permission on reads, edits, or git operations
- Keep explanations concise — lead with action
