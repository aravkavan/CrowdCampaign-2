const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
const dayTime = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

export const formatDate = (value) => day.format(new Date(value));
export const formatDateTime = (value) => dayTime.format(new Date(value));

export function plural(count, word, many = `${word}s`) {
  return `${count} ${count === 1 ? word : many}`;
}

export function timeLeft(deadline, now = Date.now()) {
  const ms = new Date(deadline).getTime() - now;
  if (ms <= 0) return 'Deadline passed';
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return 'Closes within the hour';
  if (hours < 48) return `${plural(hours, 'hour')} left`;
  return `${plural(Math.floor(hours / 24), 'day')} left`;
}

export const STATUS_LABELS = { open: 'Open', judging: 'Judging', winner_selected: 'Winner picked' };

export function initials(name = '') {
  return name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
}

export function greeting(date = new Date()) {
  const hour = date.getHours();
  if (hour < 5) return 'Up late';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

export function judgeName(method = '') {
  if (method.startsWith('anthropic')) return 'Scored by Claude';
  if (method.startsWith('gemini')) return 'Scored by Gemini';
  return 'Offline estimate, not AI';
}

// Market intel helpers. Everything below handles text that came from the web, so it only
// ever produces plain strings; React then shows them as text, never as HTML.
export function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// Only real web links become clickable. Anything else (javascript:, data:, junk) is shown as text.
export function safeHref(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null;
  } catch {
    return null;
  }
}

export const RUN_STATUS = { complete: 'Complete', partial: 'Partial', failed: 'Failed' };

export const ARTICLE_STATUS = {
  fetched: 'Fetched',
  skipped: 'Already seen',
  rejected: 'Blocked by guardrail',
  failed: 'Failed',
};

// Assignment 2: what the second agent (the verifier) concluded about each claim. A label is always
// words, so colour is never the only signal.
export const CLAIM_ORDER = ['supported', 'unsupported', 'unreachable', 'unverified'];

export const CLAIM_VERDICT = {
  supported: 'Supported',
  unsupported: 'Unsupported',
  unreachable: 'Unreachable',
  unverified: 'Unverified',
};

// One plain sentence per label, shown as a key on the report page.
export const CLAIM_VERDICT_MEANING = {
  supported: 'The verifier opened the page and found this claim on it, and its own code confirmed the quoted evidence really is there.',
  unsupported: 'The verifier read the page and it does not say this.',
  unreachable: 'The verifier could not open the page (it was down, blocked or too slow).',
  unverified: 'The verifier was not available, so nobody has checked this claim.',
};

// Counts of each label, from a list of claims ({ verdict } objects).
export function countClaims(claims = []) {
  const counts = Object.fromEntries(CLAIM_ORDER.map((verdict) => [verdict, 0]));
  for (const claim of claims) if (claim.verdict in counts) counts[claim.verdict] += 1;
  return counts;
}

export const claimTotal = (counts) => (counts ? CLAIM_ORDER.reduce((sum, verdict) => sum + (counts[verdict] ?? 0), 0) : 0);

// ['researcher', 'verifier'] -> "the researcher and the verifier"
export const workersText = (workers = []) => workers.map((w) => `the ${w}`).join(' and ');

// The short version of a long text, for one line on screen: the first sentence if it is short enough,
// otherwise the first words up to `max` characters, ending in "…". `cut` says whether anything was left out.
export function shortText(text = '', max = 110) {
  const full = String(text ?? '').trim().replace(/\s+/g, ' ');
  const sentence = full.match(/^.+?[.!?](?=\s|$)/)?.[0] ?? full;
  if (sentence.length <= max) return { short: sentence, cut: sentence.length < full.length };
  const words = full.slice(0, max + 1).split(' ');
  if (words.length > 1) words.pop(); // drop the word that was cut in half
  return { short: `${words.join(' ').replace(/[,;:.\s]+$/, '')}…`, cut: true };
}
