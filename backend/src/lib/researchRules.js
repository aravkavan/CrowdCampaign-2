// The rules for starting a research run (the agent team) from the website.
// Plain functions: no database, no clock of their own, so they can be tested alone.
//
// Why there are rules at all: every run spends the model and search keys on this computer.
//   1. One run at a time, for the whole server.
//   2. After any run the team rests (default 10 minutes).
//   3. Each person may start only a few runs per 24 hours (default 3).
// config.yaml adds a fourth limit inside the run itself: the budget.

export const DAY_MS = 24 * 60 * 60 * 1000;

const time = (value) => new Date(value).getTime();

// "about 4 minutes" or "about 3 hours": how long to wait, in words.
export function waitText(ms) {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  if (minutes < 90) return `${minutes} minute${minutes === 1 ? '' : 's'}`;
  return `${Math.ceil(minutes / 60)} hours`;
}

// rows: this server's research runs from the last 24 hours (anyone's), each { user_id, status, started_at, finished_at }.
// Returns { ok: true, runsLeft } to go ahead, or { ok: false, status, message, retryAfterSeconds, runsLeft }.
export function checkStart({ now, userId, rows, cooldownSeconds, perDay }) {
  const mine = rows.filter((row) => row.user_id === userId && time(row.started_at) > now - DAY_MS);
  const runsLeft = Math.max(0, perDay - mine.length);

  if (rows.some((row) => row.status === 'running')) {
    return {
      ok: false,
      status: 409,
      retryAfterSeconds: 60,
      runsLeft,
      message: 'The agent team is already working on a run. Wait for it to finish, then try again.',
    };
  }

  const lastFinished = Math.max(0, ...rows.map((row) => (row.finished_at ? time(row.finished_at) : 0)));
  const restLeft = lastFinished + cooldownSeconds * 1000 - now;
  if (restLeft > 0) {
    return {
      ok: false,
      status: 429,
      retryAfterSeconds: Math.ceil(restLeft / 1000),
      runsLeft,
      message: `The team rests ${waitText(cooldownSeconds * 1000)} after every run, because a run spends your keys. Try again in about ${waitText(restLeft)}.`,
    };
  }

  if (mine.length >= perDay) {
    const oldest = Math.min(...mine.map((row) => time(row.started_at)));
    const freeIn = oldest + DAY_MS - now;
    return {
      ok: false,
      status: 429,
      retryAfterSeconds: Math.ceil(freeIn / 1000),
      runsLeft: 0,
      message: `You have used your ${perDay} runs for today. The first one frees up in about ${waitText(freeIn)}.`,
    };
  }

  return { ok: true, runsLeft };
}

// A research topic as the researcher gets it: one line, at most 300 characters (the contract's limit).
export const TOPIC_MIN = 10;
export const TOPIC_MAX = 300;

export function oneLine(value) {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
}

// A starting topic from the campaign itself: its title and brief, cut at a word.
export function suggestTopic(campaign) {
  const full = oneLine(`${campaign.title}. ${campaign.brief}`);
  if (full.length <= TOPIC_MAX) return full;
  const cut = full.slice(0, TOPIC_MAX);
  const space = cut.lastIndexOf(' ');
  return (space > TOPIC_MAX * 0.6 ? cut.slice(0, space) : cut).trim();
}
