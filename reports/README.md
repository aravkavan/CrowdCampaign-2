# Reports

## Team runs (Assignment 2)

`python -m orchestrator run` writes one report here per run: `orch-run<N>.md`, where `N` is the run number in the app.
Each campaign's section lists **New since last run**, then **Still in top K**, then **Dropped**.
Under every development are its claims, each with a label (Supported, Unsupported, Unreachable or Unverified),
its source page, and, for supported claims, the evidence excerpt the verifier's code confirmed on that page.
A run where a worker failed is marked **Partial** and names the worker. The footer shows what the run spent
and how long the verifier took to wake up.

`python -m orchestrator handcheck` writes `handcheck-run<N>.md`: every unsupported claim of a run plus 10 supported
claims picked at random, each with a link and tick boxes, for the hand-check in `ORCHESTRATION.md` question 5.

A run that could not start (for example, the app is down) is not saved and doesn't use up a run number.

## Single-agent runs (Assignment 1B)

`run1.md` and `run2.md` come from the 1B tracker (one agent, no verifier), from two runs at least a day apart.
They are kept as the baseline that `ORCHESTRATION.md` question 5 compares the team with.
Each development there cites its sources with a sentence quoted from each, but has no claims and no verdicts.
