// The campaign's five fields and their rules, in one place. Creating a campaign and editing one
// use the same checks, so they can't drift apart. Nothing here touches the database.
import { HttpError } from './http.js';
import { lengthProblem, text } from './validate.js';

// [key, label used in messages, min length, max length]. The keys are column names, and only
// these keys are ever put into a query, never anything the user typed.
export const CAMPAIGN_TEXT_FIELDS = [
  ['brand', 'Brand name', 2, 80],
  ['title', 'Title', 5, 120],
  ['brief', 'Brief', 40, 4000],
  ['prize', 'Prize', 2, 120],
];

const YEAR_MS = 366 * 24 * 3600 * 1000;

// A deadline as an ISO string, or an HttpError that says what is wrong with it.
export function readDeadline(value, now = Date.now()) {
  const deadline = new Date(typeof value === 'string' ? value : Number.NaN);
  if (Number.isNaN(deadline.getTime())) {
    throw new HttpError(400, 'Deadline must be a date and time, like 2026-10-31T17:00:00Z.');
  }
  if (deadline.getTime() <= now) throw new HttpError(400, 'Pick a deadline in the future.');
  if (deadline.getTime() > now + YEAR_MS) throw new HttpError(400, 'Deadlines can be at most one year away.');
  return deadline.toISOString();
}

// Reads and checks the fields in `body`.
//   whole (default): every field is required. Used when a campaign is created.
//   partial: only the fields that were sent are checked and returned, and at least one must be
//            sent. Used when a campaign is edited, so unchanged fields (like a deadline that has
//            already passed) are never re-checked.
export function readCampaignFields(body, { partial = false, now = Date.now() } = {}) {
  const fields = {};
  for (const [key, label, min, max] of CAMPAIGN_TEXT_FIELDS) {
    if (partial && body[key] === undefined) continue;
    const value = text(body[key]);
    const problem = lengthProblem(label, value, min, max);
    if (problem) throw new HttpError(400, problem);
    fields[key] = value;
  }
  if (!partial || body.deadline !== undefined) fields.deadline = readDeadline(body.deadline, now);
  if (partial && !Object.keys(fields).length) {
    throw new HttpError(400, 'Send at least one thing to change: brand, title, brief, prize or deadline.');
  }
  return fields;
}
