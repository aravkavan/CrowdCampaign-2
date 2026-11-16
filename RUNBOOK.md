# RUNBOOK: what you do, in order

This is the part that needs **you**: your keys, your Render account, and real runs.
When it is done, `ORCHESTRATION.md` has real numbers and the demo works.

Commands are for a Mac/Linux terminal. On Windows use `py` instead of `python3`, and the notes in brackets.

---

## 0. Before you start

- [ ] **Keep the original 1B zip as a backup.** This project is the 1B copy changed into a team of three.
- [ ] **Python 3.13** is what everything was run with. 3.11 and 3.12 should work (the code parses there) but were not run.
- [ ] Keys you need: one model key (Gemini or Groq; two different ones is better), a Tavily key (search), a Render account (free), a GitHub account.
- [ ] **Set a hard spending cap on every key that can cost money**, before the first request. The assignment requires it. Two agents now call models.
- [ ] If a key was ever pasted in a chat, a zip or a repository, make a new one and delete the old.
- [ ] Your CrowdCampaign app (backend + frontend) must work as before. The backend updates its own database tables when it starts: it only **adds** a table and two columns, nothing is deleted.

## 1. Make two secret tokens

Run this **twice**. The two results must be different.

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

The first one is `RESEARCHER_TOKEN`. The second is `VERIFIER_TOKEN`.

## 2. Fill in the three `.env` files

**Shortcut:** `python3 scripts/setup_env.py` creates all three `.env` files and makes both tokens for you (same value in each place). Paste your keys, then run `python3 scripts/setup_env.py --check`: it says `ok` or `X` for every value and prints `All good.` when done. `--show verifier` prints the verifier token to paste into Render. It never prints your keys.

By hand instead: in each of these folders, copy `.env.example` to `.env` and fill it in. (`.env` files are ignored by git, so your keys stay on your computer.)

| Folder | What goes in |
|---|---|
| `services/researcher/` | `RESEARCHER_TOKEN`, one model key (`GEMINI_API_KEY` or `GROQ_API_KEY`), `TAVILY_API_KEY` |
| `services/verifier/` | `VERIFIER_TOKEN`, a model key. **Use a different model from the researcher's if you can**: a second opinion from the same model makes the same mistakes. |
| `services/orchestrator/` | both tokens (the same values as above). `TRACKER_USERNAME` and `TRACKER_PASSWORD` are already filled in with the course's public grader login (`NYUgrader`), like in 1B, so the graders see the runs when they log in. Leave them. `CROWDCAMPAIGN_API_URL` is `http://localhost:4000`. Leave `RESEARCHER_URL` and `VERIFIER_URL` empty for now. |

Every variable the project reads is listed in the root `.env.example`.

## 3. Install and try everything on your own computer first

Install the packages (once). From the project folder:

```
cd services/researcher && pip install -r requirements.txt && cd ../..
cd services/verifier && pip install -r requirements.txt && cd ../..
cd services/orchestrator && pip install -r requirements.txt && cd ../..
```

(All three need the same three packages, so one `pip install -r services/orchestrator/requirements.txt` is enough on your own computer. Windows: `py -m pip install ...`.)

Open **four terminals** and leave them open:

| Terminal | Folder | Command | You should see |
|---|---|---|---|
| 1 | `backend` | `npm start` | `CrowdCampaign API is running at http://localhost:4000` |
| 2 | `services/researcher` | `python3 -m researcher` | a line saying it listens on port 8001 |
| 3 | `services/verifier` | `python3 -m verifier` | a line saying it listens on port 8002 |
| 4 | `services/orchestrator` | `python3 -m orchestrator check` | see below |

`check` wakes both workers, tests both tokens and logs in to the app. A good answer looks like this (the times are only an example):

```
  researcher http://localhost:8001: awake after 0.0 s
             token: the token was accepted
  verifier   http://localhost:8002: awake after 0.0 s
             token: the token was accepted
  app        http://localhost:4000: logged in as <you>, 2 saved run(s) (...)
Everything is ready.
```

Then run the checks the assignment asks for, against both local workers:

```
cd ../..                              (back to the project folder)
python3 scripts/selftest.py --all
```

It prints seven checks per worker (wrong token gives 401, bad body gives 400, repeats, and so on). It costs a few cents at most. All must pass.

## 4. Deploy the verifier on Render (free tier)

First put the project on GitHub:

```
git status                            (look: no .env files and no keys in the list)
git add -A
git commit -m "Agentic orchestration"
git push
```

**Option A: Blueprint (reads `render.yaml`)**

1. Render dashboard, **New**, **Blueprint**, pick your repository.
2. Render asks for three secret values. Type them in: `VERIFIER_TOKEN` (the second token from step 1), `GEMINI_API_KEY`, `GROQ_API_KEY`. If you only have one model key, leave the other empty.
3. Apply. The first build takes a few minutes.

**Option B: by hand (same settings, if the Blueprint route does not work for you)**

New, **Web Service**, your repository, then:

| Setting | Value |
|---|---|
| Runtime | Python |
| Root Directory | `services/verifier` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python -m verifier` |
| Instance type | Free |
| Health Check Path | `/healthz` |
| Environment | `PYTHON_VERSION` = `3.13.5`, `VERIFIER_TOKEN`, and your model key(s) |

**Either way:** when it says **Live**, open `https://<your-service>.onrender.com/healthz` in a browser. It must show `{"status":"ok"}`.
Then put the address in `services/orchestrator/.env`:

```
VERIFIER_URL=https://<your-service>.onrender.com
```

Run `python3 -m orchestrator check` again. Now the verifier line shows the real cold start.
Run the self-test against the deployed verifier (the researcher keeps running locally):

```
python3 scripts/selftest.py --worker verifier --url https://<your-service>.onrender.com --token "<VERIFIER_TOKEN>"
```

(Windows PowerShell: use `py scripts\selftest.py ...`.) Write the results in the table at the bottom of `ORCHESTRATION.md`.

Keys on Render live **only** in Render's Environment settings. They are never in the repository.

## 5. Run 1 of the team

1. Make sure terminals 1 and 2 (backend, researcher) are running. The verifier is on Render.
2. **Let the verifier sit idle for 20 minutes**, so Render spins it down. Run 1 then shows a real cold start.
3. From `services/orchestrator`:

```
python3 -m orchestrator run
```

It takes a few minutes. At the end it prints the counts, the report file (`reports/orch-run<N>.md`) and the trace file.
Open the app, **Market intel**: every claim has a label, supported claims show their evidence, and the run history says complete or partial.

Your app already holds your two old runs, so this first team run is called **Run 3** in the app. In `ORCHESTRATION.md` it is "run 1" of the team (the first run in `traces/orchestrator.jsonl`). Do not reset unless you want an empty history.

### 5b. Start a run from the website instead (head person)

You can also start the team from the site. Open one of your campaigns, find **Market intel team** in the Manage box and press **Research this campaign**.

It needs three things:
1. The backend is running **on the same computer** as the project, and `backend/.env` has `ORCHESTRATOR_PYTHON` set to the Python that has the project's packages (see `backend/.env.example`). If it is missing, the box says the team is not set up.
2. The researcher is running (terminal 2) and the verifier is awake (Render or local).
3. The three `services/*/.env` files are filled in. The site never sees these keys; the orchestrator reads them itself.

Rules: one run at a time, a 10 minute rest after every run, 3 runs a day each. A run takes a few minutes and uses about a third of the normal budget. **Stop the run** ends it. The result is saved under your own login and shows in the Market intel box on that campaign and on the Market intel page.

For the real runs you write up in `ORCHESTRATION.md`, use the command line (section 5): it uses the full budget and writes `reports/orch-run<N>.md`. A site run writes no report file.

## 6. Get the verifier's log off Render

The verifier on Render keeps no files. It prints its trace to Render's log.
**Right after the run**, copy it:

1. Render dashboard, your service, **Logs**. Select all, copy, paste into a text file, for example `render-log.txt` in the project folder. (Or use the download button there if you see one.)
2. From the project folder:

```
python3 scripts/extract_trace.py render-log.txt
```

This keeps only the trace lines and writes `traces/verifier.jsonl`. Now all three logs exist: `traces/orchestrator.jsonl`, `traces/researcher.jsonl`, `traces/verifier.jsonl`.

To follow one request through all three, copy a `request_id` from the orchestrator log and search for it:

```
python3 scripts/find_trace.py <request_id>     (Windows: py scripts/find_trace.py <request_id>)
```

## 7. Fill in `ORCHESTRATION.md`

Find every blank: `python3 scripts/find_blanks.py` (Windows: `py scripts/find_blanks.py`).

| Blank | Command (from `services/orchestrator`) |
|---|---|
| Question 2: every call and its latency; where the time went | `python3 -m orchestrator stats ../../traces/orchestrator.jsonl ../../traces/researcher.jsonl ../../traces/verifier.jsonl` |
| Question 2: cold start | the line `Wake-up of the verifier` at the bottom of `reports/orch-run<N>.md` |
| Question 5: cost, time, claim counts | the same `stats` output, and the footer of `reports/orch-run<N>.md` |
| Question 5: the hand-check | `python3 -m orchestrator handcheck` (see below) |
| Self-test table | step 3 and step 4 |

**Do run 2 at least a day after run 1**, so there is something new to find (same as the last assignment). Run 2 then shows New / Still / Dropped.

**The hand-check.** `python3 -m orchestrator handcheck` prints and saves `reports/handcheck-run<N>.md`: every unsupported claim, and 10 supported claims picked at random, each with a link.
`N` is the app's run number (3 for your first team run); `--run N` picks another.
Open each link, look for the claim's words on the page, tick the boxes, count your ticks, and put the numbers in question 5.
This part is real work and cannot be skipped: nobody can do it for you, because it means reading the pages.

## 8. Be ready for the demo

- The orchestrator should be started from `services/orchestrator`. The app's backend and Neon database must be awake: open the app once the day before and again right before.
- **Wake the verifier two minutes before the demo** by opening `https://<your-service>.onrender.com/healthz` in a browser. (If you forget, the orchestrator wakes it itself; it just takes about a minute.)
- Failure demos. These runs are saved in the history, so do them **after** your real runs:
  - **Verifier down.** Mac/Linux: `VERIFIER_URL=http://localhost:9 python3 -m orchestrator run`. PowerShell: `$env:VERIFIER_URL="http://localhost:9"; py -m orchestrator run`.
    Expected: the run is **partial**, the report names the **verifier**, and every claim shows **unverified**.
  - **Researcher down.** Stop terminal 2 and run. Expected: "no new results", last run's top list shown and marked **stale**, the report names the **researcher**.
- Both failure demos still produce a report; that is the point. `python3 -m orchestrator reset` wipes the whole run history if you want a clean start (it asks first).

## 9. Before you hand it in

- [ ] `python3 scripts/find_blanks.py` says `No blanks left.`
- [ ] `python3 scripts/sync_shared.py --check` says all copies match.
- [ ] Tests pass: in each of `services/researcher`, `services/verifier`, `services/orchestrator` run `python3 -m unittest discover -s tests`; in the project folder run `python3 -m unittest discover -s tests`.
- [ ] `git status` shows no `.env` file under `services/` and no key anywhere.
- [ ] Every paid key has its spending cap set. Old keys are replaced.
- [ ] The original 1B zip is still somewhere safe.

---

## If something goes wrong

| What you see | Most likely reason | What to do |
|---|---|---|
| `check` says the token was refused (401) | The token in `services/orchestrator/.env` differs from the worker's | Copy the same value into both places. On Render, change it in Environment, then wait for the redeploy. |
| `check` says NOT awake after 120 s | Wrong `VERIFIER_URL`, or Render is still building | Open `/healthz` in a browser. Look at the Render deploy log. |
| `check` says "connection refused 3 times" | Nothing is listening at that address | Start the worker, or fix the URL/port. |
| A campaign says **researcher failed** | The researcher is not running, or its model/search key is wrong or used up | Look at terminal 2. |
| Every claim is **unverified** | The verifier failed for that campaign | The report says why (for example "rejected our token"). |
| Many claims are **unreachable** | The cited sites block bots or need a login (social networks and paywalled news are blocked on purpose) | Expected for some. It is counted in question 5. |
| A worker answers 503 and the run is slow | The free model key hit its per-minute limit; the worker asks the orchestrator to wait | Wait, or use another provider's key. |
| "None of the campaigns in config.yaml exist in the app" | The titles in `config.yaml` must match the app exactly | `cd backend && npm run seed` adds the demo campaigns. |
| The app shows no claim labels | The backend was not restarted after the update | Stop it and run `npm start` again; it updates the database itself. |
| The orchestrator cannot log in | The grader account does not exist in your database yet, or `TRACKER_USERNAME` / `TRACKER_PASSWORD` were changed | `cd backend && npm run seed` makes sure the grader account exists. Or use any user you can log in with in the app. |

## Files worth knowing

| File | What it is |
|---|---|
| `ORCHESTRATION.md` | The five written answers (graded). |
| `config.yaml` | The run: topics, K, the ONE budget and how it is split, timeouts, retries. |
| `render.yaml` | How the verifier is deployed. |
| `.env.example` | Every secret and setting, by service. |
| `services/*/config.yaml` | How each worker behaves (models, limits, fetch rules). |
| `reports/orch-run<N>.md` | The report of each team run. `reports/run1.md`, `run2.md` are the old single-agent runs. |
| `traces/*.jsonl` | The trace logs. `traces/run1.jsonl`, `run2.jsonl` are the old single-agent runs. |
