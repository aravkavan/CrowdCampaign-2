# Decisions notepad

Every decision, and what Arav said. Newest at the top of each table.
Rule: Claude adds a line here when you approve or deny something. Nothing big happens before that.
Last updated 2026-11-09 (after the "Research this campaign" button was built).

Covers the whole project: Assignment 1 (the app), 1B (the tracker) and Assignment 2 (the team of three).

## Standing rules (what you told me to do every time)

| # | Rule | What you said (2026-11-09) |
|---|------|----------------------------|
| R1 | **You approve big decisions.** When several agents (or sub-agents) are involved, they do not decide big things among themselves. Each big decision comes to you as approve or deny. | "When multiple agents ask each time a big task decision to be done, ask me, let me approve or deny, not do own things as it's risky for agents alone talking" |
| R2 | **Small parts, small edits.** The project is split into small parts. A change touches only the part (the snippet) that needs it. Nothing is regenerated as a whole new version. | "Break the whole project into small parts and snippets, and if anything has to be edited not everything will be redone, just the snippet... in future also scalable" |
| R3 | **Two notepads.** This one (decisions) and `STRUCTURE.md` (how the project is built, surface level, no code). Both kept as files in the project and as docs in Claude. | "In one notepad write about the decisions taken and what I said. In another notepad, project structure on surface level" |
| R4 | **The head person can do everything from the website.** Add, edit, pick the winner, and undo. Only the head person can undo, nobody else. | "The head should be able to do everything and have all in the website" / "undo only the head person, not all" |
| R5 | **The walkthrough covers how to do these things.** The step-by-step guide page gets a section on how the head person adds, edits, picks a winner and undoes, in the site. | "I want the walkthrough also included on how to such" (added 15:01) |

What "agents" means here, so it is clear: Claude and any helper agents it starts, and also the researcher, verifier and orchestrator in the app. For the app, the design already follows R1 in one place: the AI never picks a winner, a person does.

## What R2 means in practice

- Before a change, Claude says which part and which file it will touch.
- The change is a small edit to that spot. The other parts are not rewritten.
- If a part has grown too big, Claude proposes splitting it first and you approve the split before it happens. Done once so far: `CampaignDetail.jsx` (454 lines, four panels) became a 114-line shell plus small files in `pages/campaign/`.
- `STRUCTURE.md` has the parts map: each part, its files, and what it does.

## Decision log

| Date | Decision | What you said | Status |
|------|----------|---------------|--------|
| 2026-11-09 | **Start the agent team from the website.** Per campaign, in the Manage box: a "Research this campaign" button. The topic is filled in from the brief and you can change it. The result is saved under your own login. The server starts the Python orchestrator on the same computer. Limits: one run at a time, a 10 minute rest between runs, 3 runs a day | You chose: "Per campaign, in the Manage box (Recommended)", "Start the orchestrator on the same computer (Recommended)", "One at a time, 10 min wait, 3 a day (Recommended)" | Approved and built. Tested with fake servers and a real Python child process, not with your real database or live keys |
| 2026-11-09 | Small calls Claude made inside that design (say "change X" to reverse any): the 10 minute rest is for the whole team (every head person shares it); the 3 a day cap is per person; a site run uses 34% of the budget in `config.yaml` (so one campaign does not spend a whole 5-topic run); a site run writes no `reports/orch-run*.md` file (the result is on the site); there is a **Stop the run** button; a run is stopped by force after 25 minutes; if the server restarts mid-run, that run is shown as stopped and does not make you wait; the server gives the child program only the login token for that run, never the database or JWT secrets | | Built. Reverse any of them if you disagree |
| 2026-11-09 | One walkthrough for everything: head person tools, the split, the agents (Render, local run, real runs) and filling in `ORCHESTRATION.md` | "Do one proper walkthrough ... it should include head person and division and this 2nd assignment of agents usage" (16:14) | Approved and built: one "Head Person Walkthrough" page replaces the two |
| 2026-11-09 | Does the head-person work include the agents? | "Does this include the agents also" | Answered by the row above: yes, the button starts them |
| 2026-11-09 | Small calls Claude made inside the approved design (say "change X" to reverse any): the pick dialog starts on **nothing chosen** unless you pressed "Pick as winner" on a specific idea (so the AI's top idea is never pre-selected); the per-idea "Pick as winner" buttons stay; editing after a winner is picked is allowed and shows a "winner stays" note; moving a deadline ahead reopens the campaign (status is worked out from the data); an edit sends only the fields you changed | | Built. Reverse any of them if you disagree |
| 2026-11-09 | Build the head-person changes: Manage box with Edit, Score, Pick winner, Undo, Delete | Followed from the design answers below | **Built and tested with fake data** (65 browser checks, 16 server checks, 10 unit tests). Not yet run against your real database or the real Vite build, which could not be run here |
| 2026-11-09 | Design: **Undo** = undo the winner pick only (head person only; the campaign goes back to judging and a new pick is allowed). **Edit** = every field (brand, title, brief, prize, deadline), any time, even after a winner is picked. **Layout** = one "Manage" box at the top of the campaign page. **Split first** = split `CampaignDetail.jsx` into small files before adding anything | You chose: "Undo the winner pick (Recommended)", "Everything, any time", "One 'Manage' box at the top (Recommended)", "Yes, split first (Recommended)" | Approved and done |
| 2026-11-09 | Change the app, not just the notes: head-person screens for add, edit, pick winner, undo | "Change in the app as in UI/UX also, not just in notepad, both ways" | Approved, built |
| 2026-11-09 | Add a "how to use the head-person tools" section to the walkthrough page | "I want the walkthrough also included on how to such" and "similar walkthrough like before with the check and progress" | Approved and built: the "Head Person Walkthrough" page (32 tick steps, progress bar, copy buttons) |
| 2026-11-09 | Notepads live in both places: files in the project and docs in Claude | "Both ways" | Done |
| 2026-11-09 | The work applies to the whole project: Assignment 1, 1B and Assignment 2 (all in the 1B copy) | "As a whole for 1A, 1B and this 2 part of the project, for all, I want both changes" | Done in the 1B copy. The original (final project) copy is not here, so it is untouched. |
| 2026-11-09 | Nothing committed or pushed to GitHub from here | Earlier: "don't commit". You push yourself with GitHub Desktop. | Standing |

## Facts that came out of reading the code (not decisions)

- Anyone logged in can create a campaign and becomes its **head person** (the organizer). Only the head person can delete it, see all ideas, run the AI score and pick a winner.
- The head person can now **edit** a campaign and **undo** the winner pick, from the Manage box. Before this work neither existed, and a pick was final.
- Only the head person can edit or undo. The server refuses everyone else with an error, even the person whose idea won.
- The database allows at most one winner per campaign. A new winner can only be picked after the old pick is undone.
- The head person cannot enter ideas in their own campaign. Everyone else gets one idea per campaign.
- Fixed in the same work: on a phone-width screen the organizer's idea list used to scroll sideways (hidden text inside each score row stuck out). One CSS rule (`.score` in `styles.css`) fixed it. It was there before this work.

## Open questions

- (Answered) Run market intel from the website: yes, see the first row of the log.
- Assignment 2: the due date, the grading breakdown and what Brightspace wants submitted are still not known to Claude.

## Nov 16: Market intel for everyone, admins can run it anywhere
- Everyone who opens a campaign sees its Market intel (people writing ideas need to know what is new).
- Site admins (`RESEARCH_ADMINS`, default `NYUgrader`; add yours in `backend/.env.local`) can run the team on every campaign. Everyone else only on campaigns they launched.
- An admin's run is saved to the campaign's head person, so the brand and everyone else see it. Limits count for whoever pressed the button.
- Each finding is one short line with More / Less (campaign page and full report).
- Fixed: a refused start's reason no longer disappears.
