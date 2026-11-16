// Run from the backend folder:  npm test   (same as: node --test)
// No Python needed: a fake "spawn" stands in for the program, so this only tests how the backend watches it.
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { childEnv, cleanLine, runProcess } from '../src/lib/childRun.js';

class FakeStream extends EventEmitter {
  setEncoding() {}
}

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = new FakeStream();
  child.stderr = new FakeStream();
  child.killed = [];
  child.kill = (signal) => child.killed.push(signal);
  return child;
}

function start(overrides = {}) {
  const child = fakeChild();
  const lines = [];
  const exits = [];
  const timers = [];
  const calls = [];
  const handle = runProcess({
    command: 'python3',
    args: ['-m', 'orchestrator', 'run'],
    cwd: '/x',
    env: { A: '1' },
    secrets: ['SECRET-TOKEN-VALUE'],
    maxRuntimeMs: 1000,
    onLine: (line) => lines.push(line),
    onExit: (info) => exits.push(info),
    spawn: (...args) => {
      calls.push(args);
      return child;
    },
    setTimer: (fn, ms) => {
      timers.push({ fn, ms, cleared: false });
      return timers.length - 1;
    },
    clearTimer: (id) => {
      if (timers[id]) timers[id].cleared = true;
    },
    ...overrides,
  });
  return { child, lines, exits, timers, calls, handle };
}

test('the program is started with no shell, a fixed environment and no input', () => {
  const { calls } = start();
  const [command, args, options] = calls[0];
  assert.equal(command, 'python3');
  assert.deepEqual(args, ['-m', 'orchestrator', 'run']);
  assert.equal(options.shell, false);
  assert.deepEqual(options.env, { A: '1' });
  assert.equal(options.cwd, '/x');
  assert.deepEqual(options.stdio, ['ignore', 'pipe', 'pipe']);
});

test('the program does not inherit the server environment: only the safe names are copied', () => {
  const env = childEnv(
    { PATH: '/bin', HOME: '/h', DATABASE_URL: 'postgres://secret', JWT_SECRET: 'x'.repeat(40), GEMINI_API_KEY: 'k', ANTHROPIC_API_KEY: 'k2', TAVILY_API_KEY: 'k3' },
    { TRACKER_TOKEN: 't' },
  );
  assert.deepEqual(Object.keys(env).sort(), ['HOME', 'PATH', 'PYTHONIOENCODING', 'PYTHONUNBUFFERED', 'TRACKER_TOKEN']);
  assert.equal(env.TRACKER_TOKEN, 't');
});

test('output arrives line by line, even when a line is split across chunks', () => {
  const { child, lines } = start();
  child.stdout.emit('data', 'first line\nsecond ');
  child.stdout.emit('data', 'line\r\nthird');
  child.stdout.emit('end');
  assert.deepEqual(lines, ['first line', 'second line', 'third']);
});

test('error output (stderr) is read too, so a crash can be explained', () => {
  const { child, lines } = start();
  child.stderr.emit('data', 'Traceback (most recent call last):\nModuleNotFoundError: No module named requests\n');
  assert.deepEqual(lines, ['Traceback (most recent call last):', 'ModuleNotFoundError: No module named requests']);
});

test('a secret value never appears in a line, and control characters are removed', () => {
  assert.equal(cleanLine('calling with Bearer SECRET-TOKEN-VALUE now', ['SECRET-TOKEN-VALUE']), 'calling with Bearer [hidden] now');
  assert.equal(cleanLine('\u001b[31mred\u001b[0m text\u0007'), '[31mred[0m text');
  assert.equal(cleanLine('a'.repeat(500)).length, 220);
  const { child, lines } = start();
  child.stdout.emit('data', 'token SECRET-TOKEN-VALUE leaked?\n');
  assert.deepEqual(lines, ['token [hidden] leaked?']);
});

test('exit is reported once, with the exit code', () => {
  const { child, exits } = start();
  child.emit('close', 0, null);
  child.emit('close', 1, null);
  assert.equal(exits.length, 1);
  assert.deepEqual(exits[0], { code: 0, signal: null, error: null, stopped: null });
});

test('a program that cannot be started is reported as an error (for example Python is not installed)', () => {
  const { child, exits } = start();
  const error = Object.assign(new Error('spawn python3 ENOENT'), { code: 'ENOENT' });
  child.emit('error', error);
  assert.equal(exits[0].error.code, 'ENOENT');
});

test('a spawn that throws is also reported, not crashed', async () => {
  const exits = [];
  runProcess({
    command: 'x', args: [], cwd: '/', env: {}, maxRuntimeMs: 10, onLine() {}, onExit: (info) => exits.push(info),
    spawn: () => { throw Object.assign(new Error('bad'), { code: 'EACCES' }); },
  });
  await Promise.resolve();
  assert.equal(exits[0].error.code, 'EACCES');
});

test('stop() asks the program to end, then forces it after 5 seconds, and says the person stopped it', () => {
  const { child, exits, timers, handle } = start();
  handle.stop();
  assert.deepEqual(child.killed, ['SIGTERM']);
  assert.equal(timers[timers.length - 1].ms, 5000);
  timers[timers.length - 1].fn();
  assert.deepEqual(child.killed, ['SIGTERM', 'SIGKILL']);
  child.emit('close', null, 'SIGKILL');
  assert.equal(exits[0].stopped, 'user');
});

test('a run that goes on too long is stopped, and says it was the time limit', () => {
  const { child, exits, timers } = start();
  assert.equal(timers[0].ms, 1000);
  timers[0].fn();
  assert.deepEqual(child.killed, ['SIGTERM']);
  child.emit('close', null, 'SIGTERM');
  assert.equal(exits[0].stopped, 'timeout');
});

test('when the program ends by itself the timers are cleared and a late stop() does nothing', () => {
  const { child, timers, handle } = start();
  child.emit('close', 0, null);
  assert.ok(timers[0].cleared);
  handle.stop();
  assert.deepEqual(child.killed, []);
});
