# PolyWeather

## On Session Start
- Read the project history from memory (`project_history.md`) to understand what's been built and the current state
- Check `.claude/settings.local.json` for hooks and permissions already configured
- Auto-commit hook is set up: every Edit/Write auto-commits with "Auto-commit: update <filename>"
- Vercel deploy status hook runs after every `git push`

## Project Context
Weather temperature prediction trading system for Polymarket. Scans markets, forecasts with multi-model weather data, paper trades automatically, tracks P&L across multiple portfolios.

## Key Conventions
- **Frontend:** Vanilla JS/HTML in `public/` directory (no frameworks). Dark theme with CSS variables. Chart.js for charts.
- **Backend:** Python Vercel serverless functions in `api/`. Uses `urllib.request` (not `requests` library). `BaseHTTPRequestHandler` pattern.
- **Database:** Supabase (Postgres) with REST API. Service key for writes, anon key for reads.
- **Deployments:** Push to `main` auto-deploys to Vercel. Check status with `vercel ls`.
- **Portfolio system:** Multiple portfolios with separate strategies. "Datagrab" = unlimited capital (captures all data). Fixed-capital portfolios enforce position sizing.

## Working Preferences
- Use agent teams for parallel work across multiple files
- Auto-commit after every change (hook configured)
- Push to git frequently
- Don't ask for permission on reads, edits, or git operations
- Keep explanations concise — lead with action
