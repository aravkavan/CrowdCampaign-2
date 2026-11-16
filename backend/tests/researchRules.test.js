// Run from the backend folder:  npm test   (same as: node --test)
// No database, no packages and no Python needed: this tests the spending rules for the agent team.
import assert from 'node:assert/strict';
import test from 'node:test';
import { DAY_MS, checkStart, oneLine, suggestTopic, waitText } from '../src/lib/researchRules.js';

const NOW = Date.parse('2026-11-09T12:00:00Z');
const MIN = 60_000;
const ME = 'user-me';
const OTHER = 'user-other';
const settings = { now: NOW, userId: ME, cooldownSeconds: 600, perDay: 3 };
const at = (msAgo) => new Date(NOW - msAgo).toISOString();
const done = (user, startedAgo, finishedAgo) => ({ user_id: user, status: 'done', started_at: at(startedAgo), finished_at: at(finishedAgo) });

test('with nothing in the last day, a run may start and all runs are left', () => {
  assert.deepEqual(checkStart({ ...settings, rows: [] }), { ok: true, runsLeft: 3 });
});

test('only one run at a time, whoever started it', () => {
  const rows = [{ user_id: OTHER, status: 'running', started_at: at(2 * MIN), finished_at: null }];
  const verdict = checkStart({ ...settings, rows });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.status, 409);
  assert.match(verdict.message, /already working/);
});

test('the team rests after a run, and says for how long', () => {
  const verdict = checkStart({ ...settings, rows: [done(OTHER, 14 * MIN, 4 * MIN)] });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.status, 429);
  assert.equal(verdict.retryAfterSeconds, 6 * 60);
  assert.match(verdict.message, /rests 10 minutes/);
  assert.match(verdict.message, /about 6 minutes/);
});

test('the rest counts from when the last run finished, not when it started', () => {
  assert.equal(checkStart({ ...settings, rows: [done(ME, 40 * MIN, 11 * MIN)] }).ok, true);
  assert.equal(checkStart({ ...settings, rows: [done(ME, 40 * MIN, 9 * MIN)] }).ok, false);
});

test('a run that is stopped or failed also starts the rest, because it may have spent keys', () => {
  const rows = [{ user_id: ME, status: 'failed', started_at: at(8 * MIN), finished_at: at(2 * MIN) }];
  assert.equal(checkStart({ ...settings, rows }).ok, false);
});

test('each person gets three runs a day; the fourth is refused with the time it frees up', () => {
  const rows = [done(ME, 3 * 3600_000, 3 * 3600_000 - 5 * MIN), done(ME, 5 * 3600_000, 5 * 3600_000 - 5 * MIN), done(ME, 7 * 3600_000, 7 * 3600_000 - 5 * MIN)];
  const verdict = checkStart({ ...settings, rows });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.status, 429);
  assert.equal(verdict.runsLeft, 0);
  assert.equal(verdict.retryAfterSeconds, (DAY_MS - 7 * 3600_000) / 1000);
  assert.match(verdict.message, /used your 3 runs for today/);
  assert.match(verdict.message, /about 17 hours/);
});

test("another person's runs do not use up my three", () => {
  const rows = [done(OTHER, 3 * 3600_000, 3 * 3600_000 - 5 * MIN), done(OTHER, 5 * 3600_000, 5 * 3600_000 - 5 * MIN), done(OTHER, 7 * 3600_000, 7 * 3600_000 - 5 * MIN)];
  assert.deepEqual(checkStart({ ...settings, rows }), { ok: true, runsLeft: 3 });
});

test('runs left counts down', () => {
  const rows = [done(ME, 2 * 3600_000, 2 * 3600_000 - 5 * MIN)];
  assert.equal(checkStart({ ...settings, rows }).runsLeft, 2);
});

test('a run older than a day no longer counts', () => {
  const rows = [done(ME, DAY_MS + MIN, DAY_MS), done(ME, DAY_MS + 2 * MIN, DAY_MS + MIN), done(ME, DAY_MS + 3 * MIN, DAY_MS + 2 * MIN)];
  assert.equal(checkStart({ ...settings, rows }).runsLeft, 3);
});

test('the limits can be changed', () => {
  const rows = [done(ME, 3 * 3600_000, 3 * 3600_000 - 5 * MIN)];
  assert.equal(checkStart({ ...settings, perDay: 1, rows }).ok, false);
  assert.equal(checkStart({ ...settings, cooldownSeconds: 60, rows: [done(ME, 10 * MIN, 5 * MIN)] }).ok, true);
});

test('waiting times are said in plain words', () => {
  assert.equal(waitText(30_000), '1 minute');
  assert.equal(waitText(5 * MIN), '5 minutes');
  assert.equal(waitText(2 * 3600_000), '2 hours');
});

test('a topic is one tidy line', () => {
  assert.equal(oneLine('  oat   milk\n\nlaunches\t'), 'oat milk launches');
  assert.equal(oneLine(42), '');
});

test('the suggested topic is the title and the brief, cut at a word, never over 300 characters', () => {
  const short = suggestTopic({ title: 'Name our cold brew', brief: 'We sell oat-milk cold brew.' });
  assert.equal(short, 'Name our cold brew. We sell oat-milk cold brew.');
  const long = suggestTopic({ title: 'Title', brief: 'word '.repeat(200) });
  assert.ok(long.length <= 300);
  assert.ok(!long.endsWith(' '));
  assert.ok(long.startsWith('Title. word word'));
});
