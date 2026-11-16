# CrowdCampaign

**AI-assisted crowdsourced marketing.** Brands and organizers post a marketing brief with a deadline and a prize. Anyone with an account can pitch one idea per campaign. An AI judge scores every idea on creativity, relevance, feasibility and marketing potential, and recommends a top-ten shortlist. **The organizer, a person, always picks the winner.** The AI never can.

**Market intel, by a team of three agents (Assignments 1B and 2).** A **researcher** agent (the 1B tracker, now a web service) finds the top K recent developments around each campaign: competitor launches, notable marketing campaigns, trends. It breaks each one into claims, and every claim cites one source page. A separate **verifier** agent opens each cited page *itself* and labels the claim supported, unsupported or unreachable, without ever seeing how the researcher reached it. An **orchestrator**, plain code, starts the run, splits one budget between the two, writes the report, and is the only part that keeps state between runs. The results appear inside the app, on a **Market intel** page and on each campaign's page, so people pitching ideas build on what's current instead of repeating it.

Built for CSCI-GA.2630. Assignment 1: a full-stack app with registration, login, token authentication, account management, and data that survives restarts. Assignment 1B: an agentic tracker with its own loop, budgets, failure handling, guardrails, memory between runs, and provenance. Assignment 2 (Agentic Orchestration): the tracker becomes a team of three services that talk only over HTTP and survive the network between them: service tokens, deadlines, retries, idempotency, one budget, verdicts checked by code, and a trace log that follows one request across all three services.

```
Register / Log in  ->  Browse campaigns  ->  Open a brief  ->  Submit an idea
      ->  AI scores ideas  ->  Top 10 shortlist  ->  Organizer reviews  ->  Organizer picks the winner

python -m orchestrator run
   |-- POST /research -->  researcher :8001   search -> fetch -> notes -> top K, each development with claims
   |-- POST /verify   -->  verifier   :8002   fetches every cited page itself -> verdict + evidence (checked by code)
   `-- saves the run  -->  CrowdCampaign API  ->  PostgreSQL  ->  Market intel page + a box on each campaign page
```

| Part | Technology |
| --- | --- |
| Database | PostgreSQL on [Neon](https://neon.tech) (free tier) |
| Backend | Node.js + Express 5, port **4000** |
| Frontend | React 19 + Vite 6 + React Router 7, port **5173** |
| Passwords | scrypt (built into Node), OWASP parameters |
| Auth | Bearer tokens (JWT, HMAC-SHA256), 2-hour expiry |
| AI judge (optional) | Claude or Gemini; clearly labelled offline estimate without a key |
| Researcher, verifier, orchestrator | Python (3.13 tested), written from scratch with no agent framework; the standard library's HTTP server plus `requests`, `PyYAML` and `jsonschema` only |
| Worker model | Gemini (`gemini-3.5-flash-lite`, free tier) or Groq (`openai/gpt-oss-120b`), any OpenAI-compatible API |
| Search (researcher) | [Tavily](https://tavily.com) (free plan: 1,000 credits a month; 1 credit per basic search) |
| Verifier hosting | [Render](https://render.com) free web service (`render.yaml`) |

---

## Prerequisites

| Tool | Version | Check with |
| --- | --- | --- |
| Node.js | **20 or newer** (the current LTS is recommended) | `node --version` |
| npm | comes with Node (10 or newer) | `npm --version` |
| Python | **3.13** is what everything was run with; 3.11 and 3.12 should work (the code parses there) but were not run | `python3 --version` (Windows: `py --version`) |
| Git | any recent version | `git --version` |
| Internet | needed: the database is hosted on Neon | |
| API keys (researcher and verifier) | a free **Gemini** key ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) or **Groq** key ([console.groq.com/keys](https://console.groq.com/keys)); the researcher also needs a free **Tavily** key ([app.tavily.com](https://app.tavily.com)) | |
| Render account | free; hosts the verifier | |

No database install is needed. The connection string for the throwaway Neon database is already in `backend/.env`. No API keys are in the repository: put your own in the `.env` files of the services (step 2). **Set a hard spending cap on every paid key before the first request**: two agents now call models.

**Want the short, step-by-step version, including deploying to Render and filling in the written answers? Read [`RUNBOOK.md`](RUNBOOK.md).** The written answers to the assignment's five questions are in [`ORCHESTRATION.md`](ORCHESTRATION.md).

## Run it: the commands, in order

### 1. Bring up the backend and frontend

You need **two terminals**.

**Terminal 1: backend**

```bash
git clone https://github.com/aravkavan/crowdcampaign-2.git crowdcampaign-2
cd crowdcampaign/backend
npm install
npm run seed
npm start
```

Wait for `CrowdCampaign API is running at http://localhost:4000`. Leave this terminal open.

**Terminal 2: frontend**

```bash
cd crowdcampaign/frontend
npm install
npm run dev
```

Open **http://localhost:5173**. **Log in as the grader:** username `NYUgrader`, password `Courant2026!`

`npm run seed` is safe to run any number of times. It creates the tables if needed, makes sure the grader account exists with that password, and adds the demo data (skipping anything already there), including the three open campaigns the team researches. Every demo account (for example `maya_makes` or `brewhaus_team`) uses the password `DemoPass2026!`.

### 2. Set up the team (once)

In a **third terminal**, from the `crowdcampaign` folder:

Mac or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r services/orchestrator/requirements.txt     # all three services need the same three packages
cp services/researcher/.env.example   services/researcher/.env
cp services/verifier/.env.example     services/verifier/.env
cp services/orchestrator/.env.example services/orchestrator/.env
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r services\orchestrator\requirements.txt
copy services\researcher\.env.example   services\researcher\.env
copy services\verifier\.env.example     services\verifier\.env
copy services\orchestrator\.env.example services\orchestrator\.env
```

**Quicker:** after the install, `python3 scripts/setup_env.py` creates the three `.env` files and makes both tokens; `python3 scripts/setup_env.py --check` confirms everything is filled in. The manual steps below do the same thing.

Make **two different** secret tokens (run this twice):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then fill in the three `.env` files. They are git-ignored, and each service only ever sees its own:

| File | What goes in |
| --- | --- |
| `services/researcher/.env` | `RESEARCHER_TOKEN` (the first token), a model key (`GEMINI_API_KEY` or `GROQ_API_KEY`), `TAVILY_API_KEY` |
| `services/verifier/.env` | `VERIFIER_TOKEN` (the second token), a model key. Use a different model from the researcher's if you can: a second opinion from the same model makes the same mistakes. |
| `services/orchestrator/.env` | both tokens (the same values), and the app login. The login is already filled in with the course's public grader account (`NYUgrader`), so the runs show up when you log in as the grader. Leave `RESEARCHER_URL` and `VERIFIER_URL` empty to use `http://localhost:8001` and `:8002`. |

Every variable the project reads is listed in the root [`.env.example`](.env.example). In every new terminal, activate the environment again before running a service.

### 3. Run the team on your computer

Four terminals (backend and frontend as in step 1 if you want to look at the app):

| Terminal | Folder | Command | You should see |
| --- | --- | --- | --- |
| 1 | `backend` | `npm start` | `CrowdCampaign API is running at http://localhost:4000` |
| 2 | `services/researcher` | `python -m researcher` | it is listening on port 8001 |
| 3 | `services/verifier` | `python -m verifier` | it is listening on port 8002 |
| 4 | `services/orchestrator` | `python -m orchestrator check` | both workers awake, both tokens accepted, logged in to the app |

Then, in terminal 4:

```bash
python -m orchestrator run
```

For each campaign in `config.yaml` it asks the researcher for the top K, sends the claims to the verifier, writes `reports/orch-run<N>.md`, adds to its trace log (`traces/orchestrator.jsonl`; the two workers add to theirs), and saves the run through the app's API. Open **Market intel** in the app to see it. A run takes a few minutes.

### 4. Run it again

```bash
python -m orchestrator run
```

The second run recognizes developments it already reported, and organizes each campaign's report as **New since last run**, then **Still in top K**, then **Dropped**. Run it at least a day after the first, so there is something new to find.

### 5. Reset its saved state

```bash
python -m orchestrator reset
```

This asks for confirmation, then deletes every saved run, development, claim and article for the orchestrator's user, so the next run is run 1 again. Add `--yes` to skip the question, and `--delete-files` to also delete `reports/orch-run*.md` and the three trace logs.

### Other commands

```bash
python -m orchestrator status                  # what the app remembers
python -m orchestrator stats                   # round trips, time and tokens per service, from the three trace logs
python -m orchestrator handcheck               # the work sheet for checking the verifier by hand

python scripts/selftest.py --all               # the seven worker self-tests (401, 400, repeats, 409, 503, blocked URLs, tiny budget)
python scripts/extract_trace.py render-log.txt # turn a log copied from Render into traces/verifier.jsonl
python scripts/sync_shared.py --check          # are the shared files identical in every service? (see "Project structure")
```

The worker self-tests cost a few cents at most. `handcheck` prints (and saves as `reports/handcheck-run<N>.md`) every unsupported claim plus 10 supported ones picked at random, each with a link, for the hand-check in `ORCHESTRATION.md` question 5.

Run the unit tests (no keys, no network; the model and the web are faked):

```bash
cd services/researcher   && python -m unittest discover -s tests
cd services/verifier     && python -m unittest discover -s tests
cd services/orchestrator && python -m unittest discover -s tests
python -m unittest discover -s tests            # in the project folder: end-to-end tests of all three together
```

### Check the A1 API in one command

With the backend running:

```bash
cd crowdcampaign/backend
npm run check
```

This mirrors the A1 grading script: it registers two temporary accounts, calls every endpoint, tests all three rules (including GET, PATCH and DELETE on another user's `:id`), changes a password, deletes both accounts, and scans every response for password hashes. It prints PASS or FAIL for each of about 50 checks.

---

## The team of three (Assignment 2)

### Three services, one network

| Service | Job | Port | Token | What it may touch |
| --- | --- | --- | --- | --- |
| **Researcher** (`services/researcher/`) | The 1B tracker as a web service. `POST /research` takes a topic and K and returns ranked developments. Each development has claims, and each claim cites one source URL. | 8001 | `RESEARCHER_TOKEN` | The model API, the search API, public web pages. Not the CrowdCampaign app. It remembers nothing between runs. |
| **Verifier** (`services/verifier/`) | `POST /verify` takes claims (id, text, URL), fetches every URL itself, and returns `supported`, `unsupported` or `unreachable`, with an evidence excerpt for supported ones. | 8002 (Render in the demo) | `VERIFIER_TOKEN` | The model API and public web pages. It never sees the researcher's summaries, notes, quotes, search results or fetched pages. |
| **Orchestrator** (`services/orchestrator/`) | Plain code, no model. Starts a run, splits the one budget, calls both workers, checks their answers, writes the report, saves the run. | none (a command line) | both tokens, to call the workers | The two workers over HTTP, and the CrowdCampaign app's API. It is the only part that reads or writes run history and last run's top K. |

They are separate processes. They exchange data **only through HTTP**: no shared memory, no shared database tables, and no service imports another's code. The one thing they share is the contract, written once in `shared/contract.schema.json`. Each service has its own copy of the shared files because Render only deploys the folder of one service (`scripts/sync_shared.py` keeps the copies identical, and `--check` fails if they drift; see "Project structure").

### The contract

Every request to a worker uses the same envelope:

```json
{ "request_id": "uuid", "run_id": "uuid", "time_budget_ms": 60000,
  "budget": { "max_steps": 10, "max_fetches": 8, "max_tokens": 40000 },
  "payload": { } }
```

| Method | Path | Auth | Payload in | Result out |
| --- | --- | --- | --- | --- |
| GET | `/healthz` | none | | `{"status": "ok"}` |
| POST | `/research` | Bearer | `{"topic", "k"}` | `{"developments": [{"id", "title", "summary", "claims": [{"claim_id", "text", "source_url"}]}]}` |
| POST | `/verify` | Bearer | `{"claims": [{"claim_id", "text", "source_url"}]}` | `{"verdicts": [{"claim_id", "verdict", "evidence"}]}` |

A worker answers with `{request_id, run_id, status, usage, result, error}`. `status` is `complete`, `partial` or `failed`. `usage` is what it spent (steps, fetches, tokens, elapsed time). On `partial` or `failed`, `error` is `{"code", "message", "retryable"}` and says why. `evidence` is a string on supported verdicts and `null` otherwise.

| Code | Meaning | Retried? |
| --- | --- | --- |
| 200 | complete or partial work | no |
| 400 | the request fails the schema | never |
| 401 | missing or wrong service token (checked **before** the body, so a caller without the token learns nothing about the schema) | never |
| 409 | a reused `request_id` with a different body | never |
| 500 | failed for a reason retrying won't fix (bad model key, daily quota used up) | never |
| 503 | can't take it now: model rate limit, too many requests at once, or the same `request_id` is still being worked on. Always sends `Retry-After` | yes |

The workers check every request, and the orchestrator checks every answer against the same schema, including that **every `claim_id` in the verdicts is one it sent**. A malformed answer counts as that worker failing, is thrown away, and none of its contents reach the report.

### Who decides what

| The model decides | The code decides |
| --- | --- |
| **Researcher:** what to search for, which results to read, what each article says (notes and claims), whether two notes describe the same event, the ranking | the budgets and stopping when one runs out; that only URLs from search results can be fetched, and only through the guardrail; whether every quote is really in its article, and every number in a title, summary or claim too |
| **Verifier:** whether a page states a claim, and which excerpt shows it | whether the URL may be fetched at all; whether the excerpt is really on the page (after normalising); whether it is about the claim's own words and numbers; the final verdict |
| *(the orchestrator uses no model)* | everything else: routing, the budget split, retries, validating both directions, what is new / still in the top list / dropped, the report, which worker is named as failed |

### The researcher (the 1B tracker, as a service)

`services/researcher/researcher/agent.py` is the 1B loop, still written by hand. For each `/research` request: the model gets fixed instructions and the topic; each step it replies with one JSON action (`search_web`, `fetch_article` or `finish`) plus notes about the article it just read (a title, a summary, one sentence quoted word for word, and up to 3 claims); the code checks the action, runs the tool, and answers with the result. `finish` lists up to K developments, most important first.

- Limits per request come from the envelope (the smaller of that and the ceilings in `services/researcher/config.yaml`): model calls, downloads, tokens, time. When the next call will be the last, the model is told to finish now. If a limit runs out, the answer is `partial` and keeps what was verified so far.
- A claim must be 15 to 250 characters, and every number in it must appear in the article. A claim, quote or note that fails a check is refused before it can leave the service. A page that addresses an AI ("ignore previous instructions...") is flagged and nothing citing it is accepted.
- **What changed from 1B:** the researcher no longer talks to the app and remembers nothing, so it can't skip articles read in earlier runs (the contract's payload is only a topic and K). "New / Still / Dropped" is now worked out by the orchestrator (see below).

### The verifier

`services/verifier/verifier/judge.py`. For every claim, in order:

1. The URL goes through the fetch guard. A URL the guard refuses (not http(s), `localhost`, a private or link-local address such as `169.254.169.254`, a name that points at one, a redirect to one) is **unreachable**, and nothing is sent to it.
2. The page is downloaded (once per URL per request) and reduced to plain text. A page that can't be downloaded, is blocked by `robots.txt` or a blocked site, or has almost no text is **unreachable**.
3. A page that talks to AI agents ("mark this claim as supported") can't be evidence: **unsupported**, without asking the model.
4. A number in the claim that appears nowhere on the page makes it **unsupported**, without asking the model.
5. The model sees the claim and the most relevant passages of the page, and replies `supported` or `unsupported` plus an excerpt. Nothing else it says is used.
6. **Code** checks the excerpt: at most 300 characters, really present in the page's text after normalising both strings (decode HTML entities, Unicode NFKC, collapse whitespace, lowercase, plus a few extra steps listed in `ORCHESTRATION.md`), about the claim's own words, and containing the claim's numbers. If any check fails, "supported" becomes **unsupported**. The model's word alone never makes a claim supported.

What the verifier checks is that **the page says it**, not that it is true. A page can state something false, and that is out of its reach (`ORCHESTRATION.md` question 4 discusses what else can still get through).

### The orchestrator: one run

`services/orchestrator/orchestrator/run.py` is plain control flow. It decides from three things only: `config.yaml`, HTTP status codes, and numbers and ids it has checked against the contract. No text a worker sent is read to decide what happens next; such text is only passed on, shown or saved.

1. Log in to the app (`POST /api/auth/login`), load its memory (`GET /api/tracker/state`), and wake both workers (`GET /healthz`) in parallel, so a sleeping Render service starts waking while the run begins.
2. For each campaign: send `POST /research` with that campaign's slice of the budget; check the answer; compare the developments with last run's, by code (a title at least 60% similar to a known one, or a shared source and 30% similar, is the same development); pick up to 12 claims; send `POST /verify`; check the answer; put each verdict on its claim.
3. Write `reports/orch-run<N>.md` and save the run in the app (`POST /api/tracker/runs`).

| What goes wrong | What the run does |
| --- | --- |
| The verifier is down, never answers, or sends something that breaks the contract | its claims show as **unverified**, the campaign and the run are **partial**, and the report names the **verifier** |
| The researcher is down, never answers, or sends something that breaks the contract | "no new results" for that campaign; last run's top list is shown, marked **out of date**; the report names the **researcher** |
| A worker stops early (its time or budget ran out) and answers `partial` | what it had is used; claims that were not checked show as unverified |
| The app can't be reached at the start | the run stops before doing any work, and nothing is saved |

### One budget per run

`config.yaml` sets the total for the whole run (model calls, downloads, tokens, seconds) and how it is split: the researcher's share and the verifier's share. Before each call the orchestrator hands that worker a **slice**: what is left in its share, divided by the campaigns still to do (so what one campaign didn't use rolls forward), and never more than the time that is left. The workers enforce their slice. The orchestrator adds up the usage the workers **report** and refuses to make a call that would go past the total. Each `request_id` is charged once, however many times it was retried. A worker that never answers (or answers with something unusable) is charged its **full** slice, because it may well have spent it. Requests refused before any work (400, 401, 409) are free.

### Retries and duplicates

- **Retried** (with exponential backoff, 2 s, 4 s, 8 s ..., capped at 30 s, at most 4 tries, honouring `Retry-After` up to 60 s): connection refused or dropped, no answer in time, 502, 503, 504. Every retry resends the **identical body** with the **same `request_id`**, and the full `time_budget_ms`.
- **Never retried:** 400, 401, 409, 500. The run stops that call with a plain message.
- **Timeouts:** the orchestrator waits for the worker's own time budget plus a margin, and, until a worker has answered once, an extra allowance for a cold start (a free Render service takes about a minute to wake up).
- **Workers are idempotent.** The same `request_id` with the same JSON (key order and whitespace don't matter) returns the stored answer, with no new model calls and no new downloads. The same `request_id` with a different body is a 409. The same `request_id` **while the first copy is still running** is a 503 with `Retry-After`; the work is not started twice. Answers are kept **in memory** (the last 200, for 6 hours), so a Render restart or spin-down forgets them: a repeat after a restart is simply done again. `ORCHESTRATION.md` question 3 walks through every case.

### Guardrails on every download

The verifier and the researcher use the same guard (`guard.py`). Every check runs before any request is made:

- only `http` and `https`, only ports 80 and 443, no `user:password@` URLs, no local names (`localhost`, `*.local`, `*.internal`...), plus the blocked list in the service's `config.yaml` (sites behind a login or whose terms forbid scraping);
- the host name is **resolved**, and if **any** address is loopback, private, link-local, multicast, reserved, unspecified or otherwise not public, the URL is refused. This includes IPv6 (`::1`, `fc00::/7`, `fe80::/10`) and the cloud metadata address `169.254.169.254`. It also catches `http://2130706433/`, `http://127.1/`, `http://0x7f000001/`, `http://[::ffff:127.0.0.1]/` and names that point at private addresses;
- the connection goes to the address that was checked (no second DNS lookup), and the address actually reached is checked again;
- redirects are followed by hand, at most 4, and **every hop** goes through every check (a public URL that redirects to `127.0.0.1` or `169.254.169.254` is refused);
- one 15-second deadline covers the whole download, including servers that send one byte at a time; reading stops at 2 MB (also for compressed pages); only HTML and plain text are accepted; `robots.txt` is respected.

### Text from the web and from workers is data, never instructions

- Instructions, tools and budgets come only from the code and the config files. A model can't change them, and anything it asks for outside the allowed tools is refused.
- **Web text** reaches a model inside `<web_content>` tags, with `<` and `>` replaced so a page can't close the wrapper, and with a standing rule to ignore instructions found there.
- **A claim** is written by a model that read untrusted pages. The verifier treats it as data to check: it goes to the model inside `<claim>` tags with the same replacement, and the model's reply is reduced to `{verdict, excerpt}` and then checked by code.
- **Nothing a worker returns can change the orchestrator's routing, budget or instructions.** Their text is only passed on, shown or saved. Every verdict's `claim_id` must be one the orchestrator sent. Errors from a worker are shown only after control characters are stripped.
- In the app, everything from the web or from a worker is rendered as **text**, never HTML. Only `http(s)` links are clickable, and the API refuses to store any other kind of link. A claim like `<img src=x onerror=...>` shows up as harmless text.

### Memory between runs

The orchestrator owns all state between runs. It lives in the app's PostgreSQL database, and the orchestrator reaches it only through the app's API: it logs in as `TRACKER_USERNAME`, loads its state at the start of a run (`GET /api/tracker/state`) and saves the finished run (`POST /api/tracker/runs`). It never connects to the database, and neither worker can reach it.

- **What changed since last run:** the orchestrator compares this run's top K with the last one. **New** means first reported in this run. **Still in top K** means it was reported before. **Dropped** means it was in the last top K but not this one. A campaign the researcher could not research keeps its previous list, marked **out of date**.
- **The same development from a new URL:** the new article becomes another source of the known development instead of a new development (title similarity, as above).
- **Claims:** every run stores its own claims with their verdicts. A development carried over from an earlier run shows the claims, and verdicts, from the run that checked them, and says which run that was.

### Trace logs

Every service writes one JSON object per line for every request it receives (`direction: "in"`) and every call it makes (`direction: "out"`), with these fields: `ts, service, run_id, request_id, step, direction, endpoint_or_tool, status, latency_ms, attempt, tokens` (a few lines add a `detail` field). **Latency is measured on the caller's side**, because the machines' clocks disagree. Using `run_id` and `request_id` you can follow one request through all three logs:

```bash
grep -h "<request_id>" traces/*.jsonl
```

| Log | Written by |
| --- | --- |
| `traces/orchestrator.jsonl` | the orchestrator |
| `traces/researcher.jsonl` | the researcher |
| `traces/verifier.jsonl` | the verifier on your computer. On Render the verifier keeps no files: it prints each line to Render's log, and `scripts/extract_trace.py` turns a copy of that log into this file. |

Tokens, API keys and passwords are never written: each service strips every secret it knows from every line before writing it. `python -m orchestrator stats` adds up the round trips, time and tokens per service.

### Deploying the verifier (Render, free tier)

`render.yaml` describes the service (`rootDir: services/verifier`, build `pip install -r requirements.txt`, start `python -m verifier`, health check `/healthz`, free plan). The three secrets (`VERIFIER_TOKEN`, `GEMINI_API_KEY`, `GROQ_API_KEY`) are marked "ask me" there, so you type them into Render's **Environment** settings: they are never in the repository. Free services spin down after 15 minutes idle and take about a minute to wake: the orchestrator wakes the verifier at the start of a run and allows for the cold start in its timeouts. The full steps are in [`RUNBOOK.md`](RUNBOOK.md).

### Cost

On the free tiers a run costs nothing. `config.yaml` caps a whole run at 80 model calls and claim checks, 150 downloads, 500,000 tokens and 30 minutes, whatever the workers want. The researcher makes at most 3 searches per campaign, so at most 9 Tavily credits a run (of the free plan's 1,000 a month). The numbers a real run used are in its report footer and in `python -m orchestrator stats`, and `ORCHESTRATION.md` question 5 compares them with the single-agent tracker. Gemini is the recommended model: Groq's free tier allows 8,000 tokens per minute, so the workers spend time waiting between calls (they do this automatically) and a run can end partial on the time budget.

### Honest limits

- The verifier proves that **the page says the claim**, not that the claim is true.
- Workers report their own usage, so a buggy or hostile worker could under-report it. The wall-clock limit and the per-call slices still apply.
- The workers keep answers only in memory. After a restart a repeated request is redone (and its first attempt's spend is invisible).
- The shared helper files are **copied** into each service rather than imported from a common folder, because Render only deploys one service's folder. They are byte-identical (checked by `scripts/sync_shared.py --check`), but a strict reading of "nothing shared except a schema file" could question it.

---

## Configuration and the `.env` files

| File | Committed? | Contents |
| --- | --- | --- |
| `backend/.env` | **Yes, on purpose** | Throwaway Neon `DATABASE_URL` plus non-secret settings. The course allows a working `.env` for a throwaway database. |
| `backend/.env.example` | Yes | Every variable the backend reads, with explanations |
| `backend/.env.local` | **No** (git-ignored) | Optional personal AI keys for the A1 judge. Loaded first, so it overrides `.env`. |
| `backend/.secrets/jwt-secret` | **No** (git-ignored) | Token-signing key, generated automatically on first start |
| `frontend/.env` | Yes | `VITE_API_URL=http://localhost:4000` (not a secret) |
| `config.yaml` | Yes | The run's policy: campaigns, K, the ONE budget and how it is split, timeouts, retries (no secrets) |
| `services/researcher/config.yaml`, `services/verifier/config.yaml` | Yes | How each worker behaves: models, limits, fetch rules (no secrets) |
| `.env.example` (project root) | Yes | Every secret and setting the services read, in one place |
| `services/*/.env.example` | Yes | Each service's own, shorter list |
| `services/*/.env` | **No** (git-ignored) | That service's keys and tokens. A key is only ever visible to the service that needs it. |
| `render.yaml` | Yes | How the verifier is deployed. Its secrets are asked for in Render, never stored here. |

**No personal secrets are in this repository.** The only credential is the throwaway database's, created only for this assignment. The grader login in `services/orchestrator/.env.example` is the course's public grader account. The token-signing key is generated on each machine the first time the server starts and never leaves it. The deployed verifier's keys live in Render's Environment settings.

## Things that might surprise you

- **No migration tool.** On every start the server runs `CREATE TABLE IF NOT EXISTS ...` (see `backend/src/migrate.js`), so an empty database gets all its tables, including the tracker's and the claims table, automatically. Assignment 2 only *added* to the existing schema (two columns and one table); nothing is dropped, so existing data stays.
- **The first request after a break can take a few seconds.** Neon's free tier puts the database to sleep after 5 idle minutes and wakes it on the next query. The orchestrator retries the app API twice for this. Keep the database awake before a demo (open the app once).
- **The verifier on Render can take about a minute to answer the first request.** Free services spin down after 15 idle minutes. The orchestrator wakes it at the start of a run and allows for it in its timeouts.
- **The orchestrator needs the backend running.** Its memory is in the app's database, reached through the API, so start the backend first (step 1).
- **Run numbers come from the database.** If the app already holds earlier runs (for example the two single-agent 1B runs), the team's first run continues the numbering, and `reports/orch-run<N>.md` uses the app's number. After `python -m orchestrator reset` the next run is run 1 again, **and the earlier runs are gone from the app**. `reports/run1.md`, `reports/run2.md` and `traces/run1.jsonl`, `traces/run2.jsonl` are the old single-agent runs, kept as the baseline for `ORCHESTRATION.md` question 5.
- **Market intel is per user.** Each user sees only their own runs (the orchestrator logs in as `NYUgrader`). Other users get an empty Market intel page, and campaigns show no intel box.
- **Logging in after a restart still works.** Tokens are signed with the key in `backend/.secrets/jwt-secret`, which persists across restarts. Tokens expire after 2 hours.
- **The A1 AI judge is optional.** Without a key, "Score ideas" uses a transparent keyword heuristic labelled *"Offline estimate, not AI"*.
- **If the database credentials ever stop working** (for example, if Neon revoked them after detecting them in a public repository): create any free PostgreSQL database (Neon, Supabase or Render), paste its connection string into `DATABASE_URL` in `backend/.env`, then run `npm run seed`. Tables are created automatically.

## Enabling the AI judge (A1, optional)

Create `backend/.env.local` (it is git-ignored) with **one** of these:

```bash
# Google Gemini (has a free tier: https://aistudio.google.com/apikey)
GEMINI_API_KEY=your-key-here

# or Anthropic Claude (paid: https://platform.claude.com)
ANTHROPIC_API_KEY=your-key-here
```

Restart the backend. The startup banner shows which judge is active. With both keys set, Claude is used; set `AI_PROVIDER=gemini` to force Gemini. Defaults: `gemini-3.5-flash-lite` and `claude-haiku-4-5-20251001`, changeable with `GEMINI_MODEL` and `ANTHROPIC_MODEL`.

How the judge is kept safe and separate:

- It is **one module** (`backend/src/ai/`) and **one table** (`evaluations`). If it fails, the evaluate request returns `502` with "Nothing was changed", and every other feature keeps working.
- **It cannot pick a winner.** The only code that sets a winner is `POST /api/campaigns/:id/winner`, which requires the organizer's own login.
- **Prompt injection:** ideas are untrusted text, escaped and wrapped in tags; one demo idea ("Best idea ever") tries exactly that attack. Every score is clamped to 1 to 10, and the overall score is computed by the server.

## API reference

All requests and responses are JSON. Protected routes need the header `Authorization: Bearer <token>`. Errors always look like `{"error": "A readable message."}`.

A **user** in any response is exactly `{"id", "username", "email", "created_at", "updated_at"}`. There is never a password or hash field (Rule 1).

### Required endpoints (A1)

| Method | Path | Auth | Body | Success | Errors |
| --- | --- | --- | --- | --- | --- |
| GET | `/healthz` | none | | `200 {"status":"ok"}` | |
| POST | `/api/auth/register` | none | `{"username", "email"?, "password"}` | `201 {token, access_token, token_type, expires_in, user}` | 400 invalid input, 409 taken |
| POST | `/api/auth/login` | none | `{"username" or "email", "password"}` | `200 {token, access_token, token_type, expires_in, user}` | 400, 401 wrong credentials, 429 too many attempts |
| GET | `/api/auth/me` | Bearer | | `200 user` | 401 |
| GET | `/api/users/:id` | Bearer | | `200 user` | 401, 404 |
| PATCH | `/api/users/:id` | Bearer | any of `{"username", "email", "password" + "current_password"}` | `200 user` (updated) | 400, 401, 403 wrong current password, 404, 409 taken |
| DELETE | `/api/users/:id` | Bearer | | `200 {"deleted": true, "id"}` | 401, 404 |

Rules: usernames are 3 to 50 letters, numbers, `.`, `-` or `_` (unique, case-insensitive); email is optional; passwords are 8 to 128 characters. Login accepts the username or the email. Changing the password requires `current_password` and logs out every existing token.

### CrowdCampaign endpoints (A1)

| Method | Path | Who | Body | Success |
| --- | --- | --- | --- | --- |
| GET | `/api/campaigns` | any user | | `200 {"campaigns": [...]}` newest first |
| POST | `/api/campaigns` | any user | `{"brand", "title", "brief", "prize", "deadline"}` (ISO date) | `201 campaign` |
| GET | `/api/campaigns/:id` | any user | | `200 campaign` plus `my_submission` and `winner` |
| PATCH | `/api/campaigns/:id` | organizer | any of `{"brand", "title", "brief", "prize", "deadline"}`; send only what changed | `200 campaign` (400 if nothing to change or a value is invalid). Allowed even after a winner is picked |
| DELETE | `/api/campaigns/:id` | organizer | | `200 {"deleted": true, "id"}` |
| POST | `/api/campaigns/:id/submissions` | anyone but the organizer | `{"title", "content"}` | `201 idea` (one per person; 409 on a second, or once closed) |
| GET | `/api/campaigns/:id/submissions` | organizer | | `200 {"campaign_id", "submissions": [ranked ideas]}` |
| POST | `/api/campaigns/:id/evaluate` | organizer | `{"force": true}` to re-score all | `200 {"judge", "scored", "failed", "message", "submissions"}`; `502` if the AI is down |
| GET | `/api/campaigns/:id/shortlist` | organizer | | `200 {"campaign_id", "size": 10, "shortlist": [...]}` |
| POST | `/api/campaigns/:id/winner` | organizer | `{"submission_id"}` | `200 campaign` with `winner` (409 if a winner is already picked: undo it first) |
| GET | `/api/campaigns/:id/research` | organizer | | `200 {available, unavailable_reason, can_start, blocked_reason, retry_after_seconds, runs_left_today, topic_suggestion, run}` where `run` is the latest research run for this campaign (`status` running, done, failed or stopped, `progress` lines while running, `result` when done) | 401, 403 not the organizer, 404 |
| POST | `/api/campaigns/:id/research` | organizer | `{"topic"}` (10 to 300 characters; the page fills it in from the brief) | `202` same shape as the GET, with `run.status` "running" (and a `Retry-After` header on 429). Starts the orchestrator for this one campaign | 400 bad topic, 403, 404, 409 a run is already going, 429 resting (10 minutes) or 3 runs used today, 503 the agent team is not set up on this server |
| DELETE | `/api/campaigns/:id/research` | organizer | | `200` same shape, run stopped | 403, 404, 409 nothing is running |
| DELETE | `/api/campaigns/:id/winner` | organizer | | `200 campaign` with no winner (409 if there is none). Only the organizer can undo, never a participant and never the AI |
| GET | `/api/ai/status` | any user | | `200 {"provider", "model", "is_ai", "label"}` |

### Tracker endpoints (1B, extended in Assignment 2)

Every route needs a login (no token or a bad token: **401**). Every route reads or changes **only the logged-in user's** tracker data; another user's run id gets **404**, exactly like an id that doesn't exist (the same reasoning as Rule 3). The upload limit for these routes is 1 MB. The orchestrator is the only writer; the workers never call the app.

| Method | Path | Used by | Body | Success | Errors |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/tracker/state` | orchestrator, at the start of a run | | `200 {"next_run_number", "last_run", "seen_urls": [...], "campaigns": [{campaign_id, developments: [...with sources], last_top: [{rank, development_id}]}]}` | 401 |
| POST | `/api/tracker/runs` | orchestrator, at the end of a run | `{"started_at", "finished_at", "status", "failed_workers": ["researcher" and/or "verifier"], "stop_reason", "model", "stats", "report_markdown", "campaigns": [{campaign_id, topic, status, failed_workers, stop_reason, top: [{ref: "<development id>" or "new:1", title, summary, sources: [{url, title, quote}], claims: [{ref, text, source_url, verdict, evidence}]}]}], "articles": [...]}` | `201` run summary with `number`, `counts` and `claim_counts` | 400 invalid (including non-http(s) links, a `supported` claim without evidence, evidence on any other verdict, a `complete` run that lists a failed worker), 401, 404 unknown campaign or someone else's development, 409 saved twice at once, 413 too large |
| DELETE | `/api/tracker/state` | `python -m orchestrator reset` | | `200 {"reset": true, "deleted": {"runs", "developments"}}` | 401 |
| GET | `/api/tracker/runs` | app: run history | | `200 {"runs": [...]}` newest first, each with `status`, `failed_workers`, `counts` (new, still, dropped) and `claim_counts` (supported, unsupported, unreachable, unverified) | 401 |
| GET | `/api/tracker/runs/latest` | app: latest report | | `200 {"run": {...} or null}` | 401 |
| GET | `/api/tracker/runs/:id` | app: one run | | `200 {"run": {..., "campaigns": [{status, failed_workers, top (each development with its claims), dropped, stale_top, counts, claim_counts}], "articles": [...]}}` | 401, 404 |
| GET | `/api/tracker/campaigns/:id` | app: the box on a campaign page | | `200 {"campaign_id", "run", "topic", "status", "failed_workers", "claim_counts", "top", "dropped", "latest"}` (empty `top` if never researched; `latest` says how the newest run that included this campaign went) | 401, 404 unknown campaign |

`status` of a run or campaign is `complete`, `partial` or `failed`. `failed_workers` says which worker (`researcher`, `verifier`) failed; it is empty when none did. A claim's `verdict` is `supported`, `unsupported`, `unreachable` or `unverified` (the verifier was down or out of budget, so it was **not** checked); only a `supported` claim has `evidence`. An article's `status` (single-agent 1B runs only) is `fetched`, `skipped`, `rejected` or `failed`.

## Data model

```
users ──< campaigns (organizer_id)            deleting a user removes their campaigns,
  │           │                               ideas, scores and tracker data (ON DELETE CASCADE)
  └──────< submissions (author_id, campaign_id)
                 │   one per person per campaign; at most one winner per campaign
                 └── evaluations (AI judge only)

users ──< tracker_runs (owner_id, number, status, failed_workers)      one row per run; number is per user
              ├──< tracker_run_campaigns (campaign_id, topic, status, failed_workers)
              ├──< tracker_articles (url, status, reason, flagged)     single-agent 1B runs: every URL looked at
              ├──< tracker_top (campaign_id, rank, development_id)     each run's top K
              └──< tracker_claims (development_id, claim_ref, text, source_url, verdict, evidence)
users ──< tracker_developments (campaign_id, title, summary, first_run_id)
              └──< tracker_sources (url, quote, run_id)                the evidence for each
```

Uniqueness rules live in the database itself (case-insensitive unique indexes on username and email, one idea per person, one winner per campaign, one number per run per user, each development at most once in a run's top list, each claim at most once per run and development), so they hold even when two requests race. Two more rules are checked by the database too: a claim's `evidence` exists **exactly** when its verdict is `supported` (and is at most 300 characters), and `failed_workers` can only hold `researcher` and `verifier`.

Why this schema: it stores facts only, and works out the rest. "What the top K was last time" is the previous run's rows in `tracker_top`. **New, Still and Dropped are computed** by comparing two runs' `tracker_top` rows (`backend/src/services/trackerHistory.js`), so the orchestrator and the web page can't disagree, and a failed run never erases the last good top list. A claim belongs to the run that checked it: a development carried over to a later run shows the claims of the run that checked them.

## Security decisions

### Rule 3: someone else's `:id` returns 404, always

`GET`, `PATCH` and `DELETE /api/users/:id` return **404 "User not found."** whenever `:id` isn't the logged-in user's own id, exactly as if that account didn't exist. I chose 404 over 403 because a 403 confirms the account exists: anyone with one account could probe ids and learn which are real (account enumeration). A 404 reveals nothing, which is also how GitHub answers for private repositories you can't see. The check runs first, before the request body is read and before any database lookup (`ownAccountOnly` in `backend/src/routes/users.js`), so all three methods answer identically and an id that doesn't exist gets the very same response. Run ids in the tracker routes follow the same rule.

### Passwords (Rule 1 and hashing)

- Hashed with **scrypt**, built into Node's `crypto` module, with the OWASP-recommended cost (N = 2^17, r = 8, p = 1), a random 16-byte salt per password and a 64-byte key. Stored as `scrypt$N$r$p$salt$hash`, so the cost can be raised later; logins quietly upgrade old hashes.
- Comparison uses `crypto.timingSafeEqual`. When a username doesn't exist, the server still runs one full scrypt, so response time doesn't reveal which usernames exist, and the error message is identical.
- Hashes never leave the server: every response is built from an allow-list of five fields (`backend/src/lib/users.js`), unexpected errors return a generic message, and `npm run check` scans every response for hash-like values.
- After 10 failed logins for one username from one address, that pair is locked for 15 minutes (HTTP 429).

### Tokens (Rule 2)

- JSON Web Tokens signed with HMAC-SHA256, implemented in about 60 readable lines in `backend/src/auth/tokens.js`. The signature is checked first; only `HS256` is accepted (blocking the `"alg": "none"` trick); tokens expire after 2 hours.
- Each token carries the user's `token_version`. Changing the password increments it, so every older token stops working. Deleted accounts' tokens stop working too.
- Missing, malformed, tampered, expired or revoked token: **401** with a `WWW-Authenticate` header. This applies to the tracker endpoints too.
- Tokens are sent as `Authorization: Bearer` headers, not cookies, so cross-site request forgery doesn't apply and `SameSite` settings aren't needed. React escapes all output, which protects the stored token from injected scripts.

### CORS

The frontend (`localhost:5173`) and backend (`localhost:4000`) are different origins, so the browser sends a preflight `OPTIONS` request before each API call. The backend answers only for origins listed in `CORS_ORIGINS`, allows only the methods and headers the app uses, and lets browsers cache the answer for 10 minutes. The frontend dev server uses a fixed port (`strictPort`), so it never silently moves to a port the backend doesn't allow. The orchestrator and the workers are not browsers, so CORS doesn't apply to them; the orchestrator uses the same Bearer tokens to talk to the app.

### Service tokens (Assignment 2)

The researcher and the verifier are web services, and the verifier is on the open internet, so they are **not public**:

- Each worker requires `Authorization: Bearer <token>` on `/research` and `/verify` and answers **401** without it. Only `GET /healthz` is open (it says only `{"status": "ok"}`).
- The token is checked **first**, before the body is read or validated, so a caller without it learns nothing about the schema. It is compared in constant time.
- Each worker has its **own** token (`RESEARCHER_TOKEN`, `VERIFIER_TOKEN`), so a leaked verifier token doesn't unlock the researcher. A worker refuses the other worker's token too (`scripts/selftest.py` checks this).
- Tokens live in environment variables (the services' git-ignored `.env` files, or Render's Environment settings for the deployed verifier). They are listed, empty, in `.env.example`. Every trace line passes through a filter that strips every secret the service knows, so no log contains a token or a key.

## Project structure

```
config.yaml              the run's policy (campaigns, K, the ONE budget and how it is split, timeouts, retries)
ORCHESTRATION.md         the written answers to the assignment's five questions
RUNBOOK.md               what you do, in order: keys, Render, real runs, filling in the answers
AGENT.md                 how the single-agent tracker worked (the Assignment 1B answers, kept for reference)
render.yaml              how the verifier is deployed on Render
.env.example             every secret and setting the services read, by service
reports/                 orch-run<N>.md (one per team run), handcheck-run<N>.md;
                         run1.md and run2.md are the old single-agent runs
traces/                  orchestrator.jsonl, researcher.jsonl, verifier.jsonl (the three logs);
                         run1.jsonl and run2.jsonl are the old single-agent runs
shared/                  the ONE source of every file that more than one service needs
  contract.schema.json   the contract: envelope, payloads, answers, errors
  contract.py            loads it and checks requests and answers against it
  worker_base.py         the HTTP server both workers use: auth, validation, idempotency, deadlines, 503 rules
  guard.py               fetch guardrails (public addresses only, every redirect hop checked)
  pages.py, extract.py   downloading a page; HTML to text; spotting pages that address an AI
  llm.py                 the model client (OpenAI-compatible chat API)
  failures.py            transient vs terminal failures, retries with backoff
  settings.py            loads config.yaml and the service's .env
  textcheck.py           normalising text; quote, number and evidence checks
  trace.py               the JSON-lines trace log, with secrets stripped
services/
  researcher/            researcher/  agent.py (the loop), tools.py, prompts.py, server.py, config.py
                         config.yaml, .env.example, requirements.txt, tests/
  verifier/              verifier/    judge.py (the checks), prompts.py, server.py, config.py
                         config.yaml, .env.example, requirements.txt, tests/
  orchestrator/          orchestrator/
                           run.py        one run: research, verify, report, save
                           client.py     calls a worker: timeouts, retries, Retry-After, same body and request_id
                           budget.py     one budget, split, sliced and charged once per request_id
                           validate.py   checks every worker answer against the contract
                           matching.py   new / still / dropped, choosing claims, putting verdicts back
                           report.py     reports/orch-run<N>.md and the body saved in the app
                           appclient.py  the CrowdCampaign API (the only way to the database)
                           handcheck.py  the work sheet for the by-hand check
                           summary.py, config.py, __main__.py   stats, config, python -m orchestrator ...
                         .env.example, requirements.txt, tests/
scripts/
  sync_shared.py         copies shared/ into each service (--check: are all 28 copies identical?)
  selftest.py            the seven worker self-tests
  extract_trace.py       Render log -> traces/verifier.jsonl
tests/                   test_end_to_end.py (all three services together, fake model, fake web), test_extract_trace.py
backend/
  src/
    server.js            start-up: check settings, create tables, listen
    app.js               Express app: security headers, CORS, routes, error handling
    config.js            reads .env.local and .env
    db.js, migrate.js    connection pool and schema (A1, tracker and claims tables)
    auth/                passwords.js, tokens.js, requireAuth.js, loginThrottle.js
    routes/              auth.js, users.js, campaigns.js, ai.js, tracker.js, research.js
    services/            submissions.js (ranking), trackerHistory.js (New/Still/Dropped, claims), research.js (starts and watches a site run)
    ai/                  the A1 judge: evaluator.js, prompt.js, offline.js, providers/
    lib/                 validation, user serialization, errors, researchRules.js (the 10 minute and 3 a day rules), childRun.js (starts the Python orchestrator safely)
  scripts/               seed.js, demo-data.js, check.js
frontend/
  src/
    main.jsx, App.jsx    entry point and routes
    api.js, auth.jsx     API client and login state
    pages/               Login, Register, Home, Campaigns, NewCampaign, CampaignDetail, Intel, Account
    components/          layout, shared UI, MarketIntelBox, ClaimLabel
    styles.css           all styles (no CSS framework)
```

Each service folder contains its own copy of the shared files it needs, because Render deploys only the folder of one service. **Edit `shared/`, never a copy**, then run `python scripts/sync_shared.py`. `python scripts/sync_shared.py --check` fails if any copy differs, so the copies can't drift apart unnoticed.

Development mode with auto-restart: `npm run dev` in `backend/` restarts the API whenever a file changes.

Tests for the campaign field rules and the research rules (no database or keys needed, 34 tests): `npm test` in `backend/`. The live check of every route, including edit and undo, is `npm run check` in `backend/` with the API running.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `DATABASE_URL is not set` | Paste the Neon connection string into `backend/.env`. |
| `Port 4000 is already in use` | Another backend is running. Close that terminal (Ctrl+C), or set `PORT=4001` in `backend/.env`, `VITE_API_URL=http://localhost:4001` in `frontend/.env` and `CROWDCAMPAIGN_API_URL=http://localhost:4001` in `services/orchestrator/.env`. |
| `Port 5173 is already in use` | Another frontend is running; close it. The port is fixed on purpose (see CORS). |
| The app says it "can't reach the backend" | Start the backend (`npm start` in `backend/`) and click Try again. |
| Timed out connecting to the database | Neon was asleep or your connection dropped. Try again after a few seconds. |
| Windows PowerShell: "running scripts is disabled" | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (this also lets `Activate.ps1` run), or use Command Prompt (`.venv\Scripts\activate.bat`). |
| `Cannot find module @rollup/rollup-...` | A known npm bug with optional packages. In `frontend/`, delete `node_modules` and `package-lock.json`, then run `npm install` again. |
| `No module named researcher` (or `verifier`, `orchestrator`) | Run the command from that service's folder (for example `services/researcher`), with the environment activated. |
| `No module named yaml`, `requests` or `jsonschema` | Activate the virtual environment, then `pip install -r services/orchestrator/requirements.txt`. |
| `Address already in use` when a worker starts | Another copy is still running; close it, or start with `--port 8003` and put the matching address in `services/orchestrator/.env`. |
| `Can't start: ... is not set` or `No model API key found` | Fill in that value in that service's `.env` (copied from its `.env.example`). |
| `check` says the token was refused (401) | The token in `services/orchestrator/.env` differs from the worker's. Copy the same value into both places. On Render, change it in Environment and wait for the redeploy. |
| `check` says NOT awake | Wrong `VERIFIER_URL`, or Render is still building. Open `/healthz` in a browser. |
| `can't reach the CrowdCampaign API` | Start the backend first (step 1), then run the orchestrator. |
| `None of the campaigns in config.yaml exist in the app` | Run `npm run seed` in `backend/` (it adds the demo campaigns), or fix the titles in `config.yaml`. |
| `the API key was rejected` | The model or search key in that worker's `.env` is wrong; copy it again. |
| `daily quota used up` | The free tier's daily limit is reached; run again tomorrow, or switch provider in the worker's `config.yaml`. |
| A campaign says **researcher failed** | The researcher isn't running, or its model or search key is wrong or used up. Look at its terminal. |
| Every claim says **unverified** | The verifier failed for that campaign. The report says why (for example, it rejected our token). |
| Many claims say **unreachable** | The cited sites block bots or need a login (social networks and paywalled news are blocked on purpose). Expected for some; it is counted in `ORCHESTRATION.md` question 5. |
| A run ends `partial` | A budget ran out or a worker failed; the report says which. Raise the limit in `config.yaml` if needed. |
| The app shows no claim labels | The backend was not restarted after the update. Stop it and run `npm start` again; it updates the database itself. |
| Mac: `CERTIFICATE_VERIFY_FAILED` | Run "Install Certificates.command" from your Python folder in Applications, or reinstall `requests`. |
| Login worked, then everything says "log in again" | Tokens last 2 hours. Log in again. |
| Scores say "Offline estimate, not AI" | No A1 judge key is set. That's fine; see "Enabling the AI judge". |
