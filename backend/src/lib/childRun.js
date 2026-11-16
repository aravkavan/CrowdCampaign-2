// Starts one program (the orchestrator) and watches it. This is the only place the backend starts a process.
//
// Safety, in short:
//   * No shell. The command and its arguments are separate, so text a person typed (the research topic)
//     can never become a command.
//   * A small, fixed environment. The program does NOT inherit this server's variables: not the database
//     address, not the token-signing key, not the AI keys. It reads its own keys from services/orchestrator/.env.
//   * Every line of output is cleaned (no control characters, no secret values, short) before anyone sees it.
//   * A run that goes on too long is stopped.
import { spawn as nodeSpawn } from 'node:child_process';

// The only variables copied from this server. Python needs a few of them to find itself, its packages and the network.
const KEEP = [
  'PATH', 'Path', 'PATHEXT', 'SystemRoot', 'SYSTEMROOT', 'windir', 'COMSPEC',
  'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
  'LANG', 'LC_ALL', 'VIRTUAL_ENV',
  'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
  'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE',
];

export function childEnv(base, extra = {}) {
  const env = {};
  for (const name of KEEP) if (base[name] !== undefined) env[name] = base[name];
  return { ...env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', ...extra };
}

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g;

export function cleanLine(text, secrets = [], limit = 220) {
  let line = String(text).replace(CONTROL, '');
  for (const secret of secrets) if (secret) line = line.split(secret).join('[hidden]');
  line = line.replace(/\s+/g, ' ').trim();
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
}

// Runs `command args` in `cwd`. Calls onLine(line) for every cleaned line of output and onExit(info) exactly once:
//   info = { code, signal, error, stopped }   stopped is 'user' or 'timeout' when we ended it, else null.
// Returns { stop() }.
export function runProcess({
  command, args, cwd, env, secrets = [], maxRuntimeMs, onLine, onExit,
  spawn = nodeSpawn, setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  let settled = false;
  let stopped = null;
  let child = null;
  let timer = null;
  let killTimer = null;

  const finish = (info) => {
    if (settled) return;
    settled = true;
    clearTimer(timer);
    clearTimer(killTimer);
    onExit({ code: null, signal: null, error: null, ...info, stopped });
  };

  try {
    child = spawn(command, args, { cwd, env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (error) {
    queueMicrotask(() => finish({ error }));
    return { stop() {} };
  }
  child.on('error', (error) => finish({ error }));
  child.on('close', (code, signal) => finish({ code, signal }));

  const feed = (stream) => {
    if (!stream) return;
    let rest = '';
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      rest += chunk;
      const parts = rest.split(/\r?\n/);
      rest = parts.pop().slice(-4000); // never keep an endless line
      for (const part of parts) {
        const line = cleanLine(part, secrets);
        if (line) onLine(line);
      }
    });
    stream.on('end', () => {
      const line = cleanLine(rest, secrets);
      if (line) onLine(line);
    });
  };
  feed(child.stdout);
  feed(child.stderr);

  const end = (why) => {
    if (settled) return;
    stopped ??= why;
    try {
      child.kill('SIGTERM');
    } catch {
      // already gone
    }
    killTimer = setTimer(() => {
      try {
        child.kill('SIGKILL');
      } catch {
        // already gone
      }
    }, 5000);
  };
  timer = setTimer(() => end('timeout'), maxRuntimeMs);
  return { stop: () => end('user') };
}
