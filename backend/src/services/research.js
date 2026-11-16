// "Research this campaign": starts the agent team (the orchestrator) for one campaign and reports how it went.
//
// Who may start it: the campaign's head person, or a site admin (config.research.admins, set by RESEARCH_ADMINS),
// who may start it on EVERY campaign. This starts `python -m orchestrator run --campaign <id> --topic=<text>`
// on this computer. The orchestrator then does what it always does: asks the researcher, asks the verifier,
// checks both answers, and saves the run through /api/tracker. It uses a login token made here for the
// campaign's HEAD PERSON, valid for one run, so the result is always saved under the head person's account,
// even when an admin started it. Everyone who opens the campaign then sees it in its Market intel box.
// The limits (one at a time, the rest, runs per day) are counted for the person who pressed the button.
//
// What this file holds: who may start, the one run that is going now (in memory), and a log row per start
// (research_runs, in the database) that the limits are counted from.
import fs from 'node:fs';
import path from 'node:path';
import { signPayload } from '../auth/tokens.js';
import { config } from '../config.js';
import { query } from '../db.js';
import { childEnv, runProcess } from '../lib/childRun.js';
import { HttpError } from '../lib/http.js';
import { TOPIC_MAX, TOPIC_MIN, checkStart, oneLine, suggestTopic } from '../lib/researchRules.js';
import { lengthProblem } from '../lib/validate.js';

// Tests replace these two; nothing else does.
export const hooks = { runProcess, now: () => Date.now() };

const PROGRESS_LINES = 14; // how many recent lines of output the page can show
const RUN_TOKEN_SECONDS = 30 * 60; // the login made for one run stops working after this

let current = null; // the run going now: { id, campaignId, userId, lines, stop }
let starting = false; // true while a start is being checked, so two clicks cannot both get in

// A site admin may run the team on every campaign. Usernames are compared without caring about case.
export function isResearchAdmin(user, admins = config.research.admins) {
  const name = String(user?.username ?? '').toLowerCase();
  return Boolean(name) && admins.some((admin) => admin.toLowerCase() === name);
}

// The head person of this campaign, or a site admin.
export function canResearch(campaign, user) {
  return Boolean(campaign?.is_organizer) || isResearchAdmin(user);
}

export function researchAvailable() {
  const entry = path.join(config.research.dir, 'orchestrator', '__main__.py');
  return fs.existsSync(entry)
    ? { ok: true }
    : { ok: false, reason: 'The agent team (the services/orchestrator folder) is not next to this backend, so this server cannot start it.' };
}

// The topic from the request, or one made from the campaign when none was sent.
export function readTopic(value, campaign) {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) return suggestTopic(campaign);
  if (typeof value !== 'string') throw new HttpError(400, 'Research topic must be text.');
  const topic = oneLine(value);
  const problem = lengthProblem('Research topic', topic, TOPIC_MIN, TOPIC_MAX);
  if (problem) throw new HttpError(400, problem);
  return topic;
}

const LOST = 'The server was restarted while this was running, so the run was lost.';

// A row that says "running" while no run is going is left over from a server that stopped. It must never
// block the team for good, so it is treated as stopped. Only a START writes that to the database
// (`close: true`): at that moment nothing else is starting and nothing is running, so the row cannot be real.
// A page that is only looking changes nothing, and leaves a row alone while a start is still in flight.
function isLeftOver(row) {
  return row.status === 'running' && current?.id !== row.id && !starting;
}

// When a leftover run is taken to have ended. The server may have restarted while the program kept going on
// its own, and a run never lasts longer than its time limit, so a recent leftover counts as having just ended
// (the team rests before the next run) and an old one counts as long over (no rest).
function endOfLeftOver(row) {
  return new Date(Math.min(hooks.now(), Date.parse(row.started_at) + config.research.maxRuntimeSeconds * 1000)).toISOString();
}

// Research runs from the last 24 hours.
async function recentRows({ close = false } = {}) {
  const { rows } = await query(
    `SELECT id, user_id, status, started_at, finished_at FROM research_runs
      WHERE started_at > now() - interval '1 day' ORDER BY started_at DESC`,
  );
  for (const row of rows) {
    if (!(close ? row.status === 'running' && current?.id !== row.id : isLeftOver(row))) continue;
    row.status = 'stopped';
    row.finished_at = endOfLeftOver(row);
    if (close) {
      await query(`UPDATE research_runs SET status = 'stopped', message = $2, finished_at = $3 WHERE id = $1 AND status = 'running'`, [row.id, LOST, row.finished_at]);
    }
  }
  return rows;
}

async function latestFor(campaignId) {
  const { rows } = await query(
    `SELECT id, user_id, topic, status, message, started_at, finished_at FROM research_runs
      WHERE campaign_id = $1 ORDER BY started_at DESC LIMIT 1`,
    [campaignId],
  );
  return rows[0] ?? null;
}

// The run the orchestrator saved for this head person since this one started (its number and how it went).
async function savedRun(userId, startedAt) {
  const { rows } = await query(
    `SELECT number, status, failed_workers, finished_at FROM tracker_runs
      WHERE owner_id = $1 AND finished_at >= $2 ORDER BY number DESC LIMIT 1`,
    [userId, startedAt],
  );
  return rows[0] ? { number: rows[0].number, status: rows[0].status, failed_workers: rows[0].failed_workers } : null;
}

// Everything the campaign page needs to draw the "Research this campaign" part of the Manage box.
export async function describeResearch(campaign, user) {
  const available = researchAvailable();
  const rows = await recentRows();
  const rule = checkStart({ now: hooks.now(), userId: user.id, rows, ...ruleSettings() });
  const last = await latestFor(campaign.id);
  const running = current && current.campaignId === campaign.id ? current : null;
  if (last && isLeftOver(last)) Object.assign(last, { status: 'stopped', message: LOST, finished_at: endOfLeftOver(last) });

  let run = null;
  if (last) {
    run = {
      id: last.id,
      status: last.status,
      topic: last.topic,
      message: last.message,
      started_at: last.started_at,
      finished_at: last.finished_at,
      progress: running && running.id === last.id ? [...running.lines] : [],
      result: last.status === 'done' ? await savedRun(campaign.organizer.id, last.started_at) : null,
    };
  }
  return {
    available: available.ok,
    unavailable_reason: available.ok ? null : available.reason,
    can_start: available.ok && rule.ok,
    blocked_reason: available.ok && !rule.ok ? rule.message : null,
    retry_after_seconds: available.ok && !rule.ok ? rule.retryAfterSeconds : null,
    runs_left_today: rule.runsLeft,
    topic_suggestion: suggestTopic(campaign),
    saved_to: campaign.organizer.username, // whose account the result is saved under: always the head person's
    run,
  };
}

function ruleSettings() {
  return { cooldownSeconds: config.research.cooldownSeconds, perDay: config.research.perDay };
}

function blocked(rule) {
  const error = new HttpError(rule.status, rule.message);
  error.retryAfterSeconds = rule.retryAfterSeconds;
  return error;
}

// The head person's id and token version, for the one-run login. The caller is the head person most of the time.
async function headPerson(campaign, user) {
  if (campaign.organizer.id === user.id) return { id: user.id, token_version: user.token_version };
  const { rows } = await query('SELECT id, token_version FROM users WHERE id = $1', [campaign.organizer.id]);
  if (!rows[0]) throw new HttpError(404, 'This campaign’s head person no longer has an account, so there is nowhere to save the result.');
  return rows[0];
}

// Starts the team for `campaign`, pressed by `user` (the head person or a site admin). Throws HttpError when it
// may not start. The result is saved under the head person's account; the limits count against `user`.
export async function startResearch({ campaign, user, topic }) {
  if (current || starting) throw blocked({ status: 409, retryAfterSeconds: 60, message: 'The agent team is already working on a run. Wait for it to finish, then try again.' });
  starting = true;
  try {
    const available = researchAvailable();
    if (!available.ok) throw new HttpError(503, available.reason);
    const rule = checkStart({ now: hooks.now(), userId: user.id, rows: await recentRows({ close: true }), ...ruleSettings() });
    if (!rule.ok) throw blocked(rule);
    const head = await headPerson(campaign, user);

    const { rows } = await query(
      'INSERT INTO research_runs (campaign_id, user_id, topic) VALUES ($1, $2, $3) RETURNING id, started_at',
      [campaign.id, user.id, topic],
    );
    const id = rows[0].id;

    // A login for this one run, for the head person (so the result is saved to their campaign's history).
    // It lasts as long as the run may, no longer.
    const issued = Math.floor(hooks.now() / 1000);
    const token = signPayload({ sub: head.id, tv: head.token_version, iat: issued, exp: issued + RUN_TOKEN_SECONDS });
    const run = { id, campaignId: campaign.id, userId: user.id, lines: [], stop: () => {} };
    current = run;

    const handle = hooks.runProcess({
      command: config.research.python,
      args: ['-m', 'orchestrator', 'run', '--campaign', campaign.id, `--topic=${topic}`],
      cwd: config.research.dir,
      env: childEnv(process.env, { TRACKER_TOKEN: token, CROWDCAMPAIGN_API_URL: `http://127.0.0.1:${config.port}` }),
      secrets: [token],
      maxRuntimeMs: config.research.maxRuntimeSeconds * 1000,
      onLine: (line) => {
        run.lines.push(line);
        if (run.lines.length > PROGRESS_LINES) run.lines.shift();
      },
      onExit: (info) => {
        finishRun(run, info).catch((err) => console.error('[research] could not record how the run ended:', err.message));
      },
    });
    run.stop = handle.stop;
  } finally {
    starting = false;
  }
}

// How a finished program turns into a status and one plain sentence for the page.
export function outcomeOf(info, lines, maxMinutes) {
  if (info.stopped === 'user') return { status: 'stopped', message: 'Stopped by the head person.' };
  if (info.stopped === 'timeout') {
    return { status: 'stopped', message: `It ran longer than ${maxMinutes} minutes, so the site stopped it. Nothing from this run was saved.` };
  }
  if (info.error) {
    if (info.error.code === 'ENOENT') {
      return {
        status: 'failed',
        message: `Python was not found (the site tried "${config.research.python}"). Put the path of the Python where you ran pip install in ORCHESTRATOR_PYTHON in backend/.env.local, then restart the backend.`,
      };
    }
    return { status: 'failed', message: `The site could not start the team (${info.error.code ?? 'error'}).` };
  }
  if (info.code === 0) {
    const summary = lines.filter((line) => /^(Run \d+:|Claims:|Failed workers?:)/.test(line)).join(' ');
    return { status: 'done', message: summary || 'Finished.' };
  }
  const tail = lines.slice(-3).join(' ');
  return { status: 'failed', message: tail || `The team stopped with an error (code ${info.code ?? info.signal}).` };
}

async function finishRun(run, info) {
  try {
    const { status, message } = outcomeOf(info, run.lines, Math.round(config.research.maxRuntimeSeconds / 60));
    await query(`UPDATE research_runs SET status = $2, message = $3, finished_at = now() WHERE id = $1`, [
      run.id,
      status,
      message.slice(0, 500),
    ]);
  } finally {
    if (current === run) current = null; // only after the row is closed, so the limits never see a half-finished run
  }
}

// Stops the run that is going for this campaign (the head person pressed Stop).
export function stopResearch(campaign) {
  if (!current || current.campaignId !== campaign.id) throw new HttpError(409, 'Nothing is running for this campaign.');
  current.stop();
}

// For tests only: forget the run in memory.
export function resetForTests() {
  current = null;
  starting = false;
}
