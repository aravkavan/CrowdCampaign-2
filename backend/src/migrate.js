// The database schema. Every statement uses IF NOT EXISTS, so this runs safely on
// every server start: a brand-new empty database gets all its tables automatically.
import { withTransaction } from './db.js';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username      TEXT NOT NULL,
  email         TEXT,
  password_hash TEXT NOT NULL,
  -- Bumped on password change; tokens carrying an older number stop working.
  token_version INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Case-insensitive uniqueness: "Alice" and "alice" are the same account.
CREATE UNIQUE INDEX IF NOT EXISTS users_username_ci ON users (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS users_email_ci ON users (lower(email));

CREATE TABLE IF NOT EXISTS campaigns (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organizer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  brand        TEXT NOT NULL,
  title        TEXT NOT NULL,
  brief        TEXT NOT NULL,
  prize        TEXT NOT NULL,
  deadline     TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS campaigns_organizer_idx ON campaigns (organizer_id);

CREATE TABLE IF NOT EXISTS submissions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  author_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  content     TEXT NOT NULL,
  is_winner   BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- One idea per person per campaign.
  CONSTRAINT submissions_one_per_author UNIQUE (campaign_id, author_id)
);
-- At most one winner per campaign, enforced by the database itself.
CREATE UNIQUE INDEX IF NOT EXISTS submissions_one_winner ON submissions (campaign_id) WHERE is_winner;
CREATE INDEX IF NOT EXISTS submissions_author_idx ON submissions (author_id);

-- The AI layer writes here and nowhere else. If this table were empty the app
-- would still work; only the shortlist would be missing.
CREATE TABLE IF NOT EXISTS evaluations (
  submission_id       UUID PRIMARY KEY REFERENCES submissions(id) ON DELETE CASCADE,
  creativity          SMALLINT NOT NULL CHECK (creativity BETWEEN 1 AND 10),
  relevance           SMALLINT NOT NULL CHECK (relevance BETWEEN 1 AND 10),
  feasibility         SMALLINT NOT NULL CHECK (feasibility BETWEEN 1 AND 10),
  marketing_potential SMALLINT NOT NULL CHECK (marketing_potential BETWEEN 1 AND 10),
  overall             REAL NOT NULL,
  summary             TEXT NOT NULL,
  method              TEXT NOT NULL,
  evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Market intel tracker (Assignment 1B). Every row belongs to the user whose
-- tracker produced it (owner_id), and the tracker reads and writes these tables
-- only through the /api/tracker endpoints, never directly.

-- One row per tracker run.
CREATE TABLE IF NOT EXISTS tracker_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  number          INTEGER NOT NULL,
  started_at      TIMESTAMPTZ NOT NULL,
  finished_at     TIMESTAMPTZ NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
  stop_reason     TEXT,
  model           TEXT NOT NULL,
  stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_markdown TEXT NOT NULL DEFAULT '',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tracker_runs_owner_number UNIQUE (owner_id, number)
);

-- Which campaigns a run researched, with the topic it used for each.
CREATE TABLE IF NOT EXISTS tracker_run_campaigns (
  run_id      UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  topic       TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
  stop_reason TEXT,
  PRIMARY KEY (run_id, campaign_id)
);

-- Developments the tracker has reported, remembered across runs.
CREATE TABLE IF NOT EXISTS tracker_developments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  campaign_id  UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  summary      TEXT NOT NULL,
  first_run_id UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tracker_developments_owner_campaign ON tracker_developments (owner_id, campaign_id);

-- The articles that support each development, with the exact sentence quoted from each.
CREATE TABLE IF NOT EXISTS tracker_sources (
  development_id UUID NOT NULL REFERENCES tracker_developments(id) ON DELETE CASCADE,
  url            TEXT NOT NULL,
  title          TEXT NOT NULL DEFAULT '',
  quote          TEXT NOT NULL,
  run_id         UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  PRIMARY KEY (development_id, url)
);

-- Each run's ranked top K per campaign. The previous run's rows are "what the top K was last time".
CREATE TABLE IF NOT EXISTS tracker_top (
  run_id         UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  campaign_id    UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  rank           SMALLINT NOT NULL CHECK (rank >= 1),
  development_id UUID NOT NULL REFERENCES tracker_developments(id) ON DELETE CASCADE,
  PRIMARY KEY (run_id, campaign_id, rank),
  CONSTRAINT tracker_top_once UNIQUE (run_id, campaign_id, development_id)
);

-- Every URL a run looked at, and what happened to it.
CREATE TABLE IF NOT EXISTS tracker_articles (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id      UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  owner_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
  url         TEXT NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL CHECK (status IN ('fetched', 'skipped', 'rejected', 'failed')),
  reason      TEXT,
  http_status SMALLINT,
  bytes       INTEGER,
  fetched_at  TIMESTAMPTZ NOT NULL,
  -- true when the page contained text that looked like instructions aimed at the AI
  flagged     BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS tracker_articles_run ON tracker_articles (run_id);
CREATE INDEX IF NOT EXISTS tracker_articles_seen ON tracker_articles (owner_id, url) WHERE status = 'fetched';

-- ---------------------------------------------------------------------------
-- Agentic orchestration (Assignment 2). The orchestrator saves each run through the same
-- /api/tracker endpoints; these are the two things it adds.

-- Which worker failed in a run, and in each campaign of it: 'researcher' and/or 'verifier'.
-- An empty list means no worker failed. (ADD COLUMN IF NOT EXISTS keeps this safe on a database that already has data.)
ALTER TABLE tracker_runs
  ADD COLUMN IF NOT EXISTS failed_workers TEXT[] NOT NULL DEFAULT '{}'
  CHECK (failed_workers <@ ARRAY['researcher', 'verifier']::text[]);
ALTER TABLE tracker_run_campaigns
  ADD COLUMN IF NOT EXISTS failed_workers TEXT[] NOT NULL DEFAULT '{}'
  CHECK (failed_workers <@ ARRAY['researcher', 'verifier']::text[]);

-- The claims behind each reported development, each with the verifier's verdict for it.
--   supported    the verifier found the claim on the page and its code confirmed the quoted evidence
--   unsupported  the page was read, but it does not say this (or the quote did not check out)
--   unreachable  the page could not be fetched (down, blocked address, timeout)
--   unverified   the verifier was down or out of budget, so the claim was NOT checked
-- Evidence is the excerpt (at most 300 characters) and exists only on supported claims.
CREATE TABLE IF NOT EXISTS tracker_claims (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id         UUID NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
  development_id UUID NOT NULL REFERENCES tracker_developments(id) ON DELETE CASCADE,
  claim_ref      TEXT NOT NULL,
  ordinal        SMALLINT NOT NULL,
  text           TEXT NOT NULL,
  source_url     TEXT NOT NULL,
  verdict        TEXT NOT NULL CHECK (verdict IN ('supported', 'unsupported', 'unreachable', 'unverified')),
  evidence       TEXT CHECK (evidence IS NULL OR char_length(evidence) <= 300),
  CONSTRAINT tracker_claims_once UNIQUE (run_id, development_id, claim_ref),
  -- Evidence belongs to supported claims and to nothing else.
  CONSTRAINT tracker_claims_evidence CHECK ((verdict = 'supported') = (evidence IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS tracker_claims_development ON tracker_claims (development_id);

-- ---------------------------------------------------------------------------
-- "Research this campaign" (the head person starts the agent team from the website).
-- One row per start. It is the log the limits are counted from (one at a time, a rest after each run,
-- a few runs per person per day), so the limits survive a server restart. The research itself is saved
-- by the orchestrator through /api/tracker, as always; this table only says who started what and how it ended.
--   running  the team is working on it
--   done     the orchestrator finished and saved the run (the run itself may be complete or partial)
--   failed   it could not start, or stopped with an error (message says why)
--   stopped  the head person stopped it, it took too long, or the server restarted while it ran
CREATE TABLE IF NOT EXISTS research_runs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic       TEXT NOT NULL CHECK (char_length(topic) BETWEEN 1 AND 300),
  status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed', 'stopped')),
  message     TEXT CHECK (message IS NULL OR char_length(message) <= 600),
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS research_runs_started ON research_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS research_runs_campaign ON research_runs (campaign_id, started_at DESC);
`;

export async function migrate() {
  await withTransaction(async (client) => {
    // If the server and the seed script start at the same moment, this lock makes
    // one wait for the other instead of both creating tables at once.
    await client.query('SELECT pg_advisory_xact_lock(20260924)');
    await client.query(SCHEMA);
  });
}
