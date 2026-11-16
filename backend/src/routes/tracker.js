// /api/tracker: the market-intel tracker's memory, and what the web app shows from it.
//
// Every route needs a login (Rule 2: 401 without a valid token), and every query is
// limited to the logged-in user's own rows, so one user can never read or change
// another user's tracker data. A run id that isn't yours gets 404, exactly like an id
// that doesn't exist (the same reasoning as Rule 3 for /api/users/:id).
// The one exception is on purpose: GET /api/tracker/campaigns/:id shows ANY logged-in person the
// campaign's latest Market intel (its top list, from the head person's runs), so people writing ideas
// know what is new. It shows only that list: no run history, no articles, no report, nothing to change.
//
//   GET    /api/tracker/state           orchestrator: load memory at the start of a run
//   POST   /api/tracker/runs            orchestrator: save a finished run (with each claim's verdict)
//   DELETE /api/tracker/state           orchestrator: reset (forget every run)
//   GET    /api/tracker/runs            app: run history
//   GET    /api/tracker/runs/latest     app: the latest run, in full
//   GET    /api/tracker/runs/:id        app: one run, in full, with its articles
//   GET    /api/tracker/campaigns/:id   app: a campaign's latest intel, for everyone who opens it
import { Router } from 'express';
import { query, withTransaction } from '../db.js';
import { requireAuth } from '../auth/requireAuth.js';
import { HttpError, bodyOf } from '../lib/http.js';
import { isUuid } from '../lib/validate.js';
import { loadHistory, runArticles, runSummary } from '../services/trackerHistory.js';

export const trackerRouter = Router();
trackerRouter.use(requireAuth);

const RUN_STATUSES = new Set(['complete', 'partial', 'failed']);
const ARTICLE_STATUSES = new Set(['fetched', 'skipped', 'rejected', 'failed']);
const WORKERS = new Set(['researcher', 'verifier']);
const CLAIM_VERDICTS = new Set(['supported', 'unsupported', 'unreachable', 'unverified']);
const LIMITS = { campaigns: 10, top: 20, sources: 10, claims: 12, articles: 1000, report: 200_000 };

// ------------------------------------------------------------------ validation

function textField(value, label, { max, min = 1, optional = false }) {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim() && optional)) {
    if (optional) return null;
    throw new HttpError(400, `${label} is required.`);
  }
  if (typeof value !== 'string') throw new HttpError(400, `${label} must be text.`);
  const trimmed = value.trim();
  if (trimmed.length < min) throw new HttpError(400, `${label} is required.`);
  if (trimmed.length > max) throw new HttpError(400, `${label} is too long (at most ${max} characters).`);
  return trimmed;
}

// Only http(s) links are ever stored, so a link shown in the app can't be a javascript: URL.
function webUrl(value, label) {
  const raw = textField(value, label, { max: 2048 });
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new HttpError(400, `${label} is not a valid URL.`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new HttpError(400, `${label} must start with http:// or https://.`);
  return raw;
}

function timestamp(value, label) {
  const date = new Date(typeof value === 'string' ? value : Number.NaN);
  if (Number.isNaN(date.getTime())) throw new HttpError(400, `${label} must be a date and time.`);
  return date.toISOString();
}

function list(value, label, max) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new HttpError(400, `${label} must be a list.`);
  if (value.length > max) throw new HttpError(400, `${label} can have at most ${max} items.`);
  return value;
}

function smallInt(value, label) {
  if (value === undefined || value === null) return null;
  if (!Number.isInteger(value) || value < 0 || value > 2_147_483_647) throw new HttpError(400, `${label} must be a whole number.`);
  return value;
}

function parseSource(s, label) {
  if (!s || typeof s !== 'object') throw new HttpError(400, `${label} must be an object.`);
  return {
    url: webUrl(s.url, `${label}.url`),
    title: textField(s.title, `${label}.title`, { max: 300, optional: true }) ?? '',
    // The two-agent orchestrator keeps no per-source quote (a claim's evidence is the checked quote), so it may be empty.
    quote: textField(s.quote, `${label}.quote`, { max: 1000, optional: true }) ?? '',
  };
}

// Which workers failed: a list of 'researcher' and/or 'verifier', each at most once.
function parseFailedWorkers(value, label) {
  const items = list(value, label, WORKERS.size);
  for (const w of items) {
    if (!WORKERS.has(w)) throw new HttpError(400, `${label} can only contain researcher and verifier.`);
  }
  if (new Set(items).size !== items.length) throw new HttpError(400, `${label} lists the same worker twice.`);
  return items;
}

// One claim and the verifier's verdict on it. Evidence is the excerpt the verifier's code confirmed on the page:
// it must be there for a supported claim and must not be there for any other verdict.
function parseClaim(c, label) {
  if (!c || typeof c !== 'object') throw new HttpError(400, `${label} must be an object.`);
  if (!CLAIM_VERDICTS.has(c.verdict)) throw new HttpError(400, `${label}.verdict must be supported, unsupported, unreachable or unverified.`);
  const evidence = textField(c.evidence, `${label}.evidence`, { max: 300, optional: true });
  if (c.verdict === 'supported' && !evidence) throw new HttpError(400, `${label} is supported, so it needs evidence.`);
  if (c.verdict !== 'supported' && evidence) throw new HttpError(400, `${label} is ${c.verdict}, so it can't have evidence.`);
  return {
    ref: textField(c.ref, `${label}.ref`, { max: 64 }),
    text: textField(c.text, `${label}.text`, { max: 1000 }),
    source_url: webUrl(c.source_url, `${label}.source_url`),
    verdict: c.verdict,
    evidence,
  };
}

function parseRun(body) {
  const run = {
    started_at: timestamp(body.started_at, 'started_at'),
    finished_at: timestamp(body.finished_at, 'finished_at'),
    status: body.status,
    stop_reason: textField(body.stop_reason, 'stop_reason', { max: 500, optional: true }),
    model: textField(body.model, 'model', { max: 200 }),
    stats: body.stats ?? {},
    report_markdown: typeof body.report_markdown === 'string' ? body.report_markdown : '',
  };
  if (!RUN_STATUSES.has(run.status)) throw new HttpError(400, 'status must be complete, partial or failed.');
  run.failed_workers = parseFailedWorkers(body.failed_workers, 'failed_workers');
  if (run.status === 'complete' && run.failed_workers.length) throw new HttpError(400, 'A complete run cannot have a failed worker.');
  if (!run.stats || typeof run.stats !== 'object' || Array.isArray(run.stats)) throw new HttpError(400, 'stats must be an object.');
  if (JSON.stringify(run.stats).length > 20_000) throw new HttpError(400, 'stats is too large.');
  if (run.report_markdown.length > LIMITS.report) throw new HttpError(400, 'report_markdown is too long.');

  run.campaigns = list(body.campaigns, 'campaigns', LIMITS.campaigns).map((c, i) => {
    const label = `campaigns[${i}]`;
    if (!c || typeof c !== 'object') throw new HttpError(400, `${label} must be an object.`);
    if (!isUuid(c.campaign_id)) throw new HttpError(400, `${label}.campaign_id must be a campaign id.`);
    if (!RUN_STATUSES.has(c.status)) throw new HttpError(400, `${label}.status must be complete, partial or failed.`);
    const top = list(c.top, `${label}.top`, LIMITS.top).map((item, j) => {
      const itemLabel = `${label}.top[${j}]`;
      if (!item || typeof item !== 'object') throw new HttpError(400, `${itemLabel} must be an object.`);
      const isNew = typeof item.ref === 'string' && /^new:\d{1,4}$/.test(item.ref);
      if (!isNew && !isUuid(item.ref)) throw new HttpError(400, `${itemLabel}.ref must be a development id or "new:<n>".`);
      const sources = list(item.sources, `${itemLabel}.sources`, LIMITS.sources).map((s, k) => parseSource(s, `${itemLabel}.sources[${k}]`));
      if (isNew && !sources.length) throw new HttpError(400, `${itemLabel} is new, so it needs at least one source.`);
      const claims = list(item.claims, `${itemLabel}.claims`, LIMITS.claims).map((cl, k) => parseClaim(cl, `${itemLabel}.claims[${k}]`));
      const claimRefs = claims.map((cl) => cl.ref);
      if (new Set(claimRefs).size !== claimRefs.length) throw new HttpError(400, `${itemLabel}.claims lists the same claim twice.`);
      return {
        ref: item.ref,
        isNew,
        title: isNew ? textField(item.title, `${itemLabel}.title`, { max: 200 }) : null,
        summary: isNew ? textField(item.summary, `${itemLabel}.summary`, { max: 1000 }) : null,
        sources,
        claims,
      };
    });
    const refs = top.map((t) => t.ref);
    if (new Set(refs).size !== refs.length) throw new HttpError(400, `${label}.top lists the same development twice.`);
    if (c.status === 'failed' && top.length) throw new HttpError(400, `${label} failed, so it can't have a top list.`);
    const failedWorkers = parseFailedWorkers(c.failed_workers, `${label}.failed_workers`);
    if (c.status === 'complete' && failedWorkers.length) throw new HttpError(400, `${label} is complete, so it can't have a failed worker.`);
    return {
      campaign_id: c.campaign_id.toLowerCase(),
      topic: textField(c.topic, `${label}.topic`, { max: 300 }),
      status: c.status,
      stop_reason: textField(c.stop_reason, `${label}.stop_reason`, { max: 500, optional: true }),
      failed_workers: failedWorkers,
      top,
    };
  });
  if (!run.campaigns.length) throw new HttpError(400, 'A run must include at least one campaign.');
  const campaignIds = run.campaigns.map((c) => c.campaign_id);
  if (new Set(campaignIds).size !== campaignIds.length) throw new HttpError(400, 'campaigns lists the same campaign twice.');

  run.articles = list(body.articles, 'articles', LIMITS.articles).map((a, i) => {
    const label = `articles[${i}]`;
    if (!a || typeof a !== 'object') throw new HttpError(400, `${label} must be an object.`);
    if (a.campaign_id !== null && a.campaign_id !== undefined && !isUuid(a.campaign_id)) {
      throw new HttpError(400, `${label}.campaign_id must be a campaign id or null.`);
    }
    if (!ARTICLE_STATUSES.has(a.status)) throw new HttpError(400, `${label}.status must be fetched, skipped, rejected or failed.`);
    // Rejected URLs are logged as given (that's the point of the log), so they are kept as
    // plain text; only the other statuses must be real http(s) links.
    const url = a.status === 'rejected' ? textField(a.url, `${label}.url`, { max: 2048 }) : webUrl(a.url, `${label}.url`);
    return {
      campaign_id: a.campaign_id ? a.campaign_id.toLowerCase() : null,
      url,
      title: textField(a.title, `${label}.title`, { max: 300, optional: true }) ?? '',
      status: a.status,
      reason: textField(a.reason, `${label}.reason`, { max: 300, optional: true }),
      http_status: smallInt(a.http_status, `${label}.http_status`),
      bytes: smallInt(a.bytes, `${label}.bytes`),
      fetched_at: timestamp(a.fetched_at, `${label}.fetched_at`),
      flagged: a.flagged === true,
    };
  });
  return run;
}

// ------------------------------------------------------------------ tracker endpoints

// GET /api/tracker/state -> everything the tracker needs to remember between runs
trackerRouter.get('/state', async (req, res) => {
  const owner = req.user.id;
  const [{ history, lastTop, devById }, seen] = await Promise.all([
    loadHistory(owner),
    query(
      `SELECT a.url, min(r.number) AS run_number, min(a.fetched_at) AS fetched_at
         FROM tracker_articles a JOIN tracker_runs r ON r.id = a.run_id
        WHERE a.owner_id = $1 AND a.status = 'fetched'
        GROUP BY a.url
        ORDER BY a.url`,
      [owner],
    ),
  ]);

  const byCampaign = new Map();
  for (const dev of devById.values()) {
    if (!byCampaign.has(dev.campaign_id)) byCampaign.set(dev.campaign_id, { campaign_id: dev.campaign_id, developments: [], last_top: [] });
    byCampaign.get(dev.campaign_id).developments.push({
      id: dev.id,
      title: dev.title,
      summary: dev.summary,
      first_run: dev.first_run,
      first_seen_at: dev.first_seen_at,
      sources: dev.sources,
    });
  }
  for (const [campaignId, top] of lastTop) {
    if (!byCampaign.has(campaignId)) byCampaign.set(campaignId, { campaign_id: campaignId, developments: [], last_top: [] });
    byCampaign.get(campaignId).last_top = top;
  }

  const last = history[history.length - 1] ?? null;
  res.json({
    next_run_number: (last?.number ?? 0) + 1,
    last_run: last
      ? { id: last.id, number: last.number, started_at: last.started_at, finished_at: last.finished_at, status: last.status, failed_workers: last.failed_workers }
      : null,
    seen_urls: seen.rows,
    campaigns: [...byCampaign.values()],
  });
});

// POST /api/tracker/runs -> save one finished run, all or nothing
trackerRouter.post('/runs', async (req, res) => {
  const owner = req.user.id;
  const run = parseRun(bodyOf(req));

  const campaignIds = run.campaigns.map((c) => c.campaign_id);
  const { rows: found } = await query('SELECT id FROM campaigns WHERE id = ANY($1::uuid[])', [campaignIds]);
  if (found.length !== campaignIds.length) throw new HttpError(404, 'Campaign not found.');

  // Existing developments must be yours and belong to the same campaign.
  const refs = run.campaigns.flatMap((c) => c.top.filter((t) => !t.isNew).map((t) => ({ id: t.ref.toLowerCase(), campaign: c.campaign_id })));
  if (refs.length) {
    const { rows } = await query('SELECT id, campaign_id FROM tracker_developments WHERE owner_id = $1 AND id = ANY($2::uuid[])', [
      owner,
      refs.map((r) => r.id),
    ]);
    const known = new Map(rows.map((r) => [r.id, r.campaign_id]));
    for (const ref of refs) {
      if (known.get(ref.id) !== ref.campaign) throw new HttpError(404, 'Development not found.');
    }
  }

  let saved;
  try {
    saved = await withTransaction(async (client) => {
      const { rows: numberRows } = await client.query(
        'SELECT COALESCE(MAX(number), 0) + 1 AS next FROM tracker_runs WHERE owner_id = $1',
        [owner],
      );
      const { rows: runRows } = await client.query(
        `INSERT INTO tracker_runs (owner_id, number, started_at, finished_at, status, stop_reason, model, stats, report_markdown, failed_workers)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
         RETURNING id, number`,
        [owner, numberRows[0].next, run.started_at, run.finished_at, run.status, run.stop_reason, run.model, JSON.stringify(run.stats), run.report_markdown, run.failed_workers],
      );
      const runId = runRows[0].id;
      const claimRows = [];

      for (const c of run.campaigns) {
        await client.query(
          `INSERT INTO tracker_run_campaigns (run_id, campaign_id, topic, status, stop_reason, failed_workers)
           VALUES ($1, $2, $3, $4, $5, $6)`,
          [runId, c.campaign_id, c.topic, c.status, c.stop_reason, c.failed_workers],
        );
        let rank = 0;
        for (const item of c.top) {
          rank += 1;
          let devId = item.ref.toLowerCase();
          if (item.isNew) {
            const { rows } = await client.query(
              `INSERT INTO tracker_developments (owner_id, campaign_id, title, summary, first_run_id)
               VALUES ($1, $2, $3, $4, $5) RETURNING id`,
              [owner, c.campaign_id, item.title, item.summary, runId],
            );
            devId = rows[0].id;
          }
          for (const s of item.sources) {
            // A new article reporting a development we already know becomes another
            // supporting source for it; the same URL twice is ignored.
            await client.query(
              `INSERT INTO tracker_sources (development_id, url, title, quote, run_id)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (development_id, url) DO NOTHING`,
              [devId, s.url, s.title, s.quote, runId],
            );
          }
          await client.query(
            'INSERT INTO tracker_top (run_id, campaign_id, rank, development_id) VALUES ($1, $2, $3, $4)',
            [runId, c.campaign_id, rank, devId],
          );
          item.claims.forEach((claim, i) => claimRows.push({ development_id: devId, ordinal: i + 1, ...claim }));
        }
      }

      if (claimRows.length) {
        // One statement for every claim of the run (at most 10 campaigns x 20 developments x 12 claims).
        await client.query(
          `INSERT INTO tracker_claims (run_id, development_id, claim_ref, ordinal, text, source_url, verdict, evidence)
           SELECT $1, k.development_id, k.ref, k.ordinal, k.text, k.source_url, k.verdict, k.evidence
             FROM jsonb_to_recordset($2::jsonb) AS k(
               development_id uuid, ref text, ordinal smallint, text text, source_url text, verdict text, evidence text)`,
          [runId, JSON.stringify(claimRows)],
        );
      }

      if (run.articles.length) {
        // One statement for the whole article log: a single round trip to the database.
        await client.query(
          `INSERT INTO tracker_articles
             (run_id, owner_id, campaign_id, url, title, status, reason, http_status, bytes, fetched_at, flagged)
           SELECT $1, $2, a.campaign_id, a.url, a.title, a.status, a.reason, a.http_status, a.bytes, a.fetched_at, a.flagged
             FROM jsonb_to_recordset($3::jsonb) AS a(
               campaign_id uuid, url text, title text, status text, reason text,
               http_status smallint, bytes integer, fetched_at timestamptz, flagged boolean)`,
          [runId, owner, JSON.stringify(run.articles)],
        );
      }
      return runRows[0];
    });
  } catch (err) {
    if (err.code === '23505' && err.constraint === 'tracker_runs_owner_number') {
      throw new HttpError(409, 'Another run was saved at the same moment. Run the tracker again.');
    }
    throw err;
  }

  const { history } = await loadHistory(owner);
  const detail = history.find((h) => h.id === saved.id);
  res.status(201).json(runSummary(detail));
});

// DELETE /api/tracker/state -> forget every run, development and article (yours only)
trackerRouter.delete('/state', async (req, res) => {
  const owner = req.user.id;
  const result = await withTransaction(async (client) => {
    const devs = await client.query('DELETE FROM tracker_developments WHERE owner_id = $1', [owner]);
    const runs = await client.query('DELETE FROM tracker_runs WHERE owner_id = $1', [owner]);
    return { runs: runs.rowCount, developments: devs.rowCount };
  });
  res.json({ reset: true, deleted: result });
});

// ------------------------------------------------------------------ app endpoints

// GET /api/tracker/runs -> run history, newest first
trackerRouter.get('/runs', async (req, res) => {
  const { history } = await loadHistory(req.user.id);
  res.json({ runs: history.map(runSummary).reverse() });
});

async function runDetail(owner, pick) {
  const { history } = await loadHistory(owner);
  const run = pick(history);
  if (!run) return null;
  return { ...run, articles_summary: run.articles, articles: await runArticles(run.id, owner) };
}

// GET /api/tracker/runs/latest -> the latest run in full ({ run: null } before the first run)
trackerRouter.get('/runs/latest', async (req, res) => {
  res.json({ run: await runDetail(req.user.id, (h) => h[h.length - 1]) });
});

// GET /api/tracker/runs/:id -> one of your runs in full; anyone else's id is a 404
trackerRouter.get('/runs/:id', async (req, res) => {
  const id = String(req.params.id).toLowerCase();
  if (!isUuid(id)) throw new HttpError(404, 'Run not found.');
  const run = await runDetail(req.user.id, (h) => h.find((r) => r.id === id));
  if (!run) throw new HttpError(404, 'Run not found.');
  res.json({ run });
});

// GET /api/tracker/campaigns/:id -> the campaign's most recent top K, for everyone who opens the campaign
//   It comes from the head person's runs: the site's "Research this campaign" always saves there, even when a
//   site admin pressed it. If the head person's runs never covered this campaign, the viewer's own runs are
//   tried (someone who researched it from the command line still sees their own result).
//   `latest` says how the newest run that included this campaign went, so the page can warn when
//   that run could not research it and the list shown is from an earlier run.
trackerRouter.get('/campaigns/:id', async (req, res) => {
  const id = String(req.params.id).toLowerCase();
  if (!isUuid(id)) throw new HttpError(404, 'Campaign not found.');
  const { rows } = await query('SELECT id, organizer_id FROM campaigns WHERE id = $1', [id]);
  if (!rows[0]) throw new HttpError(404, 'Campaign not found.');

  const owners = [rows[0].organizer_id];
  if (req.user.id !== rows[0].organizer_id) owners.push(req.user.id);
  let history = [];
  for (const owner of owners) {
    ({ history } = await loadHistory(owner));
    if (history.some((r) => r.campaigns.some((c) => c.campaign_id === id))) break;
  }
  let latest = null;
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const entry = history[i].campaigns.find((c) => c.campaign_id === id);
    if (!entry) continue;
    if (!latest) latest = { run: history[i].number, status: entry.status, failed_workers: entry.failed_workers };
    if (entry.status !== 'failed') {
      const r = history[i];
      return res.json({
        campaign_id: id,
        run: { id: r.id, number: r.number, finished_at: r.finished_at, status: r.status },
        topic: entry.topic,
        status: entry.status,
        failed_workers: entry.failed_workers,
        claim_counts: entry.claim_counts,
        top: entry.top,
        dropped: entry.dropped,
        latest,
      });
    }
  }
  return res.json({
    campaign_id: id, run: null, topic: null, status: null, failed_workers: [], claim_counts: null, top: [], dropped: [], latest,
  });
});
