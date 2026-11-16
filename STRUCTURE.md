# Project structure notepad (surface level, no code)

How CrowdCampaign is built: what software, where it is used, what it is for, and why the others were not picked.
Covers all three parts: Assignment 1 (the app), 1B (the tracker, now the researcher) and Assignment 2 (the team of three).
Last updated 2026-11-09 (after the "Research this campaign" button). Decisions live in `DECISIONS.md`.

## 1. The idea in four lines

- Brands post a challenge: a brief, a prize and a deadline.
- Anyone else sends in one idea.
- The AI scores the ideas and shows a top 10. It never picks the winner.
- The head person (the organizer) picks the winner.

Around that sits a market-intel team (researcher, verifier, orchestrator) that watches the web for each campaign and shows what is new.

## 2. The software, where it is used, and why

| Job | Software | Where in the project | Why this one | Why not the others |
|-----|----------|----------------------|--------------|--------------------|
| Pages you see | **React 19** | `frontend/src/` | Pages are made of small pieces (components) that can be changed one at a time. Fits rule R2. | Plain JavaScript gets messy once there are many pages. Angular is heavier to learn. |
| Build and dev server | **Vite 6** | `frontend/` | Starts in a second, rebuilds only what changed. | Create React App is no longer kept up. Webpack needs far more setup. |
| Moving between pages | **React Router 7** | `frontend/src/App.jsx` | Standard for React. Handles the address bar and "not found". | Hand-made routing breaks back/forward buttons. |
| API server | **Node.js 20+ with Express 5** | `backend/src/` | Same language as the pages (JavaScript). Small and plain: each route is a few lines you can read. | Django or Flask means a second language for this part. NestJS is too big for a prototype. |
| Database | **PostgreSQL, hosted on Neon** | `backend/src/db.js`, `migrate.js` | It enforces rules itself. Example: "one winner per campaign" is a rule in the database, so even two clicks at once cannot break it. Neon is free and online. | MongoDB does not enforce rules like that. SQLite is a file on one laptop, so it cannot be shared or deployed. Firebase locks you to one vendor. |
| Talking to the database | **pg** (plain SQL) | `backend/src/routes/*.js` | You can see the exact question asked of the database. | ORMs like Prisma or Sequelize hide the SQL, so rules are harder to check. |
| Passwords | **scrypt** (built into Node) | `backend/src/auth/passwords.js` | Slow and memory-hungry on purpose, so guessing from a stolen database is very slow. No extra package to install. | Plain text or MD5 or SHA are far too fast to crack. bcrypt needs a native add-on and cuts long passwords. |
| Staying logged in | **JWT** (signed token, HS256) | `backend/src/auth/tokens.js`, token kept by `frontend/src/api.js` | The server does not keep a session list. The signature proves the token is real. | Server sessions need a store to share. Cookies need extra care because the pages and the API run on different addresses. |
| Brute-force guard | A small in-memory counter | `backend/src/auth/loginThrottle.js` | After 10 failed logins for one name and address, that pair is locked for 15 minutes. | A shared store (Redis) is only needed with several servers. |
| Browser safety | **helmet** and a **CORS** allow-list | `backend/src/app.js` | Safe headers by default. Only the known page address may read the API. | Open CORS lets any website call the API. |
| Secrets | **.env files**, never in Git | `backend/.env`, `services/*/.env`, Render settings | Keys stay on your machine and in Render. | Keys in code end up in GitHub. |
| AI scoring | **Gemini** or **Anthropic**, plus an **offline estimate** | `backend/src/ai/`, `routes/ai.js` | The app works with no AI at all. The offline estimate is clearly labelled "not AI". The AI only scores. | One locked-in vendor would break the app when a key runs out. Letting the AI pick the winner is against the idea of the product. |
| Researcher, verifier, orchestrator | **Python 3.13**, standard library server, plus `requests`, `PyYAML`, `jsonschema` | `services/`, `shared/` | The assignment bans agent frameworks. Three small packages cover everything. | LangChain-style frameworks hide the decisions. Flask or FastAPI add what the standard library already does. |
| Starting the team from the site | **Node `child_process.spawn`** (built into Node), no shell | `backend/src/lib/childRun.js`, `services/research.js` | The site's server starts the Python orchestrator itself, on the same computer, hands it a short login token, and reads what it prints. Nothing else to install or run. | A job queue (Redis, BullMQ) is for many servers and many jobs; this is one run at a time. Running it through a shell would let a typed topic become a command, so no shell is used. |
| Web search for the researcher | **Tavily** | `services/researcher` | Built for AI use and has a free tier. | Scraping a search page breaks and may be blocked. |
| Models for the workers | **Gemini** (researcher) and **Groq** (verifier) | `shared/llm.py` | Two different models make a more independent second reading. Each has its own free quota. | One model for both would repeat its own mistakes. |
| Hosting the verifier | **Render** (free) | `render.yaml`, `services/verifier` | The assignment names it. Free. | Fly.io or a cloud function also work, but were not required. Cost: the free one sleeps and has a cold start. |
| Tests | **Python unittest** (agents) and **Node's built-in test runner** (campaign field rules) | `tests/`, `services/*/tests/`, `backend/tests/` | Nothing to install. Fake model and fake web, so no keys. Run the Node ones with `npm test` in `backend`. | Jest or Vitest add packages and setup for no gain here. |

## 3. Where the password is hashed

The password is hashed **on the server**, never in the browser, and the database only ever holds the hash.

| Moment | What happens | File |
|--------|--------------|------|
| Register | The server hashes the password with a fresh random salt, saves only the hash | `routes/auth.js` using `auth/passwords.js` |
| Log in | The server hashes what you typed with the saved salt and compares the two in constant time. If the username does not exist it still does the same slow work, so nobody can tell which names exist | `routes/auth.js` |
| Change password | Checks the old password, hashes the new one, and bumps a number so every older login stops working | `routes/users.js` |
| Settings get stronger later | At the next login the hash is quietly redone with the newer settings | `auth/passwords.js` |

The hash is stored in the `users` table, column `password_hash`. The salt and settings are part of that one value, so there is nothing else to look after.

## 4. Who can do what (today)

| Action | Visitor | Any logged-in user | Head person (organizer) of that campaign |
|--------|---------|--------------------|-------------------------------------------|
| Read the campaign list and pages | No, log in first | Yes | Yes |
| Create a campaign (add) | No | Yes (you become its head person) | Yes |
| Edit a campaign (brand, title, brief, prize, deadline) | No | No | Yes, any field, any time, even after a winner is picked |
| Send an idea | No | Yes, one per campaign, not in your own | No (cannot enter own campaign) |
| See all ideas and the AI top 10 | No | No, only your own idea | Yes |
| Run the AI score | No | No | Yes |
| Pick the winner | No | No | Yes |
| Undo a winner pick | No | No (not even the idea that won) | Yes. The campaign goes back to judging and a new pick is allowed |
| Delete a campaign | No | No | Yes, cannot be undone |
| Start the agent team for this campaign ("Research this campaign") | No | No | Yes. One run at a time, a 10 minute rest between runs, 3 runs a day each |
| Stop a running research run | No | No | Yes |

Rule R4: the head person does all of this from the website, in one "Manage" box at the top of the campaign page. Every one of these is also checked on the server, so hiding a button is never the only protection.

Starting the researcher, verifier and orchestrator is on the site too (the "Market intel team" part of the Manage box). You can still start them as a command on your computer. That is how you do the real, long runs for `ORCHESTRATION.md`.

## 5. What happens on the main actions

**Add a campaign:** the form (`NewCampaign.jsx`) sends brand, title, brief, prize and deadline. The server checks lengths and that the deadline is in the future (and within a year). It saves the row with you as organizer. The five fields and their limits live in one small file on each side (`backend/src/lib/campaignFields.js` and `frontend/src/lib/campaignForm.js`), so adding and editing use the same rules.

**Edit a campaign:** the head person opens "Edit campaign" in the Manage box. The form is pre-filled. Only the fields that changed are sent. The server checks that you are the organizer, then checks only those fields, so an old deadline does not block a typo fix. If the brief changed, the box suggests "Re-score all" so the AI scores match the new brief.

**Send an idea:** the participant panel (`pages/campaign/SubmitIdea.jsx`) sends title and text. The server checks that you are not the organizer, that the campaign is still open, and that you have not sent one already (the database also blocks a second one).

**AI shortlist:** the head person clicks "Score new ideas" (or "Re-score all") in the Manage box. The server scores every idea (AI or the offline estimate) and keeps the top 10. If the AI fails, nothing changes and the head person can still read every idea.

**Pick the winner:** the head person clicks "Pick winner" in the Manage box (or "Pick as winner" on one idea), chooses the idea in a small dialog, and confirms. Nothing is pre-chosen for them. The server checks they are the organizer, that no winner exists, and that the idea belongs to this campaign. Then it marks it. The database refuses a second winner even if two requests arrive together.

**Undo the pick:** the head person clicks "Undo winner pick" and confirms. The server checks they are the organizer and that a winner exists, then clears it. Anyone else gets a refusal. Then a new pick is allowed.

**Market intel run:** the orchestrator calls the researcher, then the verifier, checks both answers, and saves the run through the app's API. The Market intel page shows each claim with a label: supported, unsupported, unreachable or unverified.

**Research this campaign (from the site):** the head person presses the button, checks the topic (filled in from the brief) and confirms. The server checks they are the organizer, that the agent team is set up, and the limits (one at a time, 10 minute rest, 3 a day, counted from a database table so a restart cannot reset them). It gives the orchestrator a 30 minute login token for that person and only a short list of environment settings (never the database address or the signing secret), and starts it for this one campaign at 34% of the normal budget. The page asks again every 3 seconds and shows the team's progress lines as plain text. The result is saved under the head person's own login and the Market intel box reloads. **Stop the run** ends it. A run is stopped by force after 25 minutes.

## 6. The parts map (for small edits, rule R2)

Each row is one part. A change to one part should not need a change to the others.

| Part | Files | What it does | Size note |
|------|-------|--------------|-----------|
| Login and accounts | `backend/src/auth/*`, `routes/auth.js`, `routes/users.js`, `frontend/src/pages/Login.jsx`, `Register.jsx`, `Account.jsx`, `auth.jsx` | Register, log in, change password | Small, separate |
| Campaign list and new campaign | `routes/campaigns.js` (list, create), `pages/Campaigns.jsx`, `NewCampaign.jsx`, `components/CampaignRow.jsx` | List and add | Small |
| Campaign field rules (shared by add and edit) | `backend/src/lib/campaignFields.js` (+ `backend/tests/campaignFields.test.js`), `frontend/src/lib/campaignForm.js`, `components/CampaignFields.jsx` | The five fields, their limits and messages | Small. Change a limit in both lib files |
| Campaign page shell | `pages/CampaignDetail.jsx` (114 lines) | Loads the campaign, shows the header and facts, puts the pieces below in place | Small |
| Campaign page, participant side | `pages/campaign/ParticipantPanel.jsx`, `SubmitIdea.jsx`, `WinnerBanner.jsx` | Send an idea, see the result | One component per file |
| Campaign page, head-person side | `pages/campaign/ManageBox.jsx` (buttons and confirmations), `EditCampaignForm.jsx`, `PickWinnerDialog.jsx`, `JudgingList.jsx` (the ranked list), `IdeaEntries.jsx` (one idea), `useJudging.js` (ideas, judge, scoring); server: `routes/campaigns.js` (edit, winner, undo, delete), `routes/ai.js` | Edit, score, pick, undo, delete | One job per file. Change the buttons in `ManageBox.jsx` only |
| AI scoring | `backend/src/ai/*`, `routes/ai.js` | Score ideas, top 10 | Separate |
| Database shape | `backend/src/migrate.js`, `db.js` | Tables and rules | One file |
| Market intel in the app | `routes/tracker.js`, `services/trackerHistory.js`, `pages/Intel.jsx`, `components/MarketIntelBox.jsx`, `ClaimLabel.jsx` | Save and show runs | Separate |
| Researcher | `services/researcher/` | Finds the top 5 per campaign | Separate service |
| Verifier | `services/verifier/` | Checks each claim against its page | Separate service |
| Orchestrator | `services/orchestrator/` | Runs the team, one budget, report. Can run one campaign with a given login token (`--campaign`, `--topic`, `TRACKER_TOKEN`) | Separate service |
| Research button, site side | `pages/campaign/ResearchPanel.jsx` (the part you see), `ResearchDialog.jsx` (topic window), `useResearch.js` (start, stop, ask again every 3 s). Shown inside `ManageBox.jsx` | Start, watch and stop a run | One job per file |
| Research button, server side | `routes/research.js` (GET status, POST start, DELETE stop), `services/research.js` (one run at a time, saves the log), `lib/researchRules.js` (the 10 minute and 3 a day rules, pure and tested), `lib/childRun.js` (starts the Python program safely, cleans its output), table `research_runs` in `migrate.js`, settings in `config.js` | Starts the orchestrator and keeps the limits | One job per file |
| Shared code | `shared/` | Contract, guard, page reading, trace log. Copied into each service by `scripts/sync_shared.py` | Edit only here, then sync |
| Settings | `config.yaml`, `.env` files, `render.yaml` | Campaigns, budget, secrets, deploy | Small |
| Notes | `README.md`, `RUNBOOK.md`, `ORCHESTRATION.md`, `AGENT.md`, `DECISIONS.md`, this file | Guides and answers | Small |

`scripts/setup_env.py` creates and checks the three `services/*/.env` files (makes the tokens, never prints keys).

The shared folder is the one place where a change is copied into several services. Run `python scripts/sync_shared.py --check` after touching it.

## 7. What is hard to change today, and what is easy

- Easy: the researcher, verifier and orchestrator. Each is a folder with small files.
- Easy now: the head-person screens. The old 454-line `CampaignDetail.jsx` is split into a 114-line shell and one small file per job (see the parts map). Adding a new head-person button means a change in `ManageBox.jsx` and, if it needs the server, one small route in `routes/campaigns.js`.
- Still shared: the limits for the five campaign fields exist twice (server and browser), by design, so the browser can answer fast and the server stays the real guard. Change both together.
- Still hard: `frontend/src/styles.css` is one 2,500-line file. New head-person styles were added as one block (`manage box`), but splitting it by page is not done.
- Easy now: the research button. The limits are in `lib/researchRules.js` and the settings (`RESEARCH_*` in `backend/.env`), so changing "3 a day" or "10 minutes" is one setting, not a code change.
- Needs your computer: the button only works where the server can start Python and reach both workers (researcher on 8001, verifier on 8002 or Render). On a host without Python the box says the team is not set up.
