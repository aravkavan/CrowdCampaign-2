// Run from the backend folder:  npm test   (same as: node --test)
// No database and no packages needed: this only tests the field rules.
import assert from 'node:assert/strict';
import test from 'node:test';
import { HttpError } from '../src/lib/http.js';
import { readCampaignFields, readDeadline } from '../src/lib/campaignFields.js';

const NOW = Date.parse('2026-11-09T12:00:00Z');
const DAY = 24 * 3600 * 1000;
const good = {
  brand: 'Sunny Sip',
  title: 'Get students talking',
  brief: 'A brief that is clearly longer than forty characters, so it passes.',
  prize: '$500 and a feature',
  deadline: new Date(NOW + 14 * DAY).toISOString(),
};

function messageOf(fn) {
  try {
    fn();
  } catch (err) {
    assert.ok(err instanceof HttpError, 'should be an HttpError');
    assert.equal(err.status, 400);
    return err.message;
  }
  assert.fail('expected an error');
}

test('a whole, valid campaign comes back trimmed, with an ISO deadline', () => {
  const fields = readCampaignFields({ ...good, brand: '  Sunny Sip  ' }, { now: NOW });
  assert.deepEqual(fields, { ...good, deadline: good.deadline });
});

test('creating: every field is required, and the first problem is the one reported', () => {
  assert.equal(messageOf(() => readCampaignFields({ ...good, brand: '' }, { now: NOW })), 'Brand name needs at least 2 characters.');
  assert.equal(messageOf(() => readCampaignFields({ ...good, title: 'abc' }, { now: NOW })), 'Title needs at least 5 characters.');
  assert.equal(messageOf(() => readCampaignFields({ ...good, brief: 'too short' }, { now: NOW })), 'Brief needs at least 40 characters.');
  assert.equal(messageOf(() => readCampaignFields({ ...good, prize: 'x'.repeat(121) }, { now: NOW })), 'Prize can be at most 120 characters.');
  // Brand AND title are wrong: brand is reported, because it comes first.
  assert.equal(messageOf(() => readCampaignFields({ ...good, brand: '', title: '' }, { now: NOW })), 'Brand name needs at least 2 characters.');
  assert.equal(messageOf(() => readCampaignFields({}, { now: NOW })), 'Brand name needs at least 2 characters.');
});

test('values that are not strings count as empty', () => {
  assert.equal(messageOf(() => readCampaignFields({ ...good, title: 12345 }, { now: NOW })), 'Title needs at least 5 characters.');
  assert.equal(messageOf(() => readCampaignFields({ ...good, brand: null }, { now: NOW })), 'Brand name needs at least 2 characters.');
});

test('deadline rules: a real date, in the future, within a year', () => {
  assert.equal(messageOf(() => readDeadline('soon', NOW)), 'Deadline must be a date and time, like 2026-10-31T17:00:00Z.');
  assert.equal(messageOf(() => readDeadline(undefined, NOW)), 'Deadline must be a date and time, like 2026-10-31T17:00:00Z.');
  assert.equal(messageOf(() => readDeadline(new Date(NOW - DAY).toISOString(), NOW)), 'Pick a deadline in the future.');
  assert.equal(messageOf(() => readDeadline(new Date(NOW).toISOString(), NOW)), 'Pick a deadline in the future.');
  assert.equal(messageOf(() => readDeadline(new Date(NOW + 400 * DAY).toISOString(), NOW)), 'Deadlines can be at most one year away.');
  assert.equal(readDeadline('2026-11-20T17:00:00Z', NOW), '2026-11-20T17:00:00.000Z');
});

test('editing: only the fields that were sent are checked and returned', () => {
  assert.deepEqual(readCampaignFields({ title: '  A better title  ' }, { partial: true, now: NOW }), { title: 'A better title' });
  assert.deepEqual(readCampaignFields({ prize: '$1,000', brand: 'New Brand' }, { partial: true, now: NOW }), { brand: 'New Brand', prize: '$1,000' });
});

test('editing: unsent fields are never re-checked, so an old deadline does not block a typo fix', () => {
  // The campaign's deadline is long past, but the edit does not send it.
  const fields = readCampaignFields({ title: 'Fixed typo in the title' }, { partial: true, now: NOW });
  assert.equal('deadline' in fields, false);
});

test('editing: a field that is sent must still pass its rule', () => {
  assert.equal(messageOf(() => readCampaignFields({ title: 'abc' }, { partial: true, now: NOW })), 'Title needs at least 5 characters.');
  assert.equal(messageOf(() => readCampaignFields({ brief: '' }, { partial: true, now: NOW })), 'Brief needs at least 40 characters.');
  assert.equal(messageOf(() => readCampaignFields({ deadline: new Date(NOW - DAY).toISOString() }, { partial: true, now: NOW })), 'Pick a deadline in the future.');
});

test('editing: a deadline that is sent and valid is returned as an ISO string', () => {
  assert.deepEqual(readCampaignFields({ deadline: '2026-12-01T17:00:00Z' }, { partial: true, now: NOW }), { deadline: '2026-12-01T17:00:00.000Z' });
});

test('editing: sending nothing to change is an error', () => {
  assert.match(messageOf(() => readCampaignFields({}, { partial: true, now: NOW })), /at least one thing to change/);
  assert.match(messageOf(() => readCampaignFields({ unknown: 'x' }, { partial: true, now: NOW })), /at least one thing to change/);
});

test('editing: fields we do not know about are ignored, never returned', () => {
  const fields = readCampaignFields({ title: 'A fine new title', organizer_id: 'someone-else', id: 'x' }, { partial: true, now: NOW });
  assert.deepEqual(Object.keys(fields), ['title']);
});
