// The agent team for one campaign, as the head person sees it: can a run start, what is it doing now,
// how did the last run end. It asks the server (GET /api/campaigns/:id/research), starts a run (POST),
// stops it (DELETE), and asks again every few seconds while the team is working.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api.js';

const POLL_MS = 3000;

export default function useResearch(campaignId, onFinished) {
  const [state, setState] = useState(null); // the server's answer; null until the first one
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false); // a start or a stop request is on its way
  const path = `/api/campaigns/${campaignId}/research`;

  // `keepError`: after a refused start or stop, reload the limits but keep the reason on screen.
  const load = useCallback(async ({ keepError = false } = {}) => {
    try {
      setState(await api(path));
      if (!keepError) setError('');
    } catch (err) {
      setError(err.message);
    }
  }, [path]);

  useEffect(() => {
    load();
  }, [load]);

  // While the team is working, ask again every few seconds.
  const running = state?.run?.status === 'running';
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  // When a run that was going has ended, tell the page once (so the Market intel box can reload).
  const finished = useRef(onFinished);
  useEffect(() => {
    finished.current = onFinished;
  });
  const lastStatus = useRef(null);
  useEffect(() => {
    const status = state?.run?.status ?? null;
    if (lastStatus.current === 'running' && status && status !== 'running') finished.current?.(state.run);
    lastStatus.current = status;
  }, [state]);

  const send = useCallback(
    async (method, body) => {
      setBusy(true);
      setError('');
      try {
        setState(await api(path, { method, body }));
        return true;
      } catch (err) {
        setError(err.message);
        load({ keepError: true }); // show the limits as they are now, and keep the reason
        return false;
      } finally {
        setBusy(false);
      }
    },
    [path, load],
  );

  const start = useCallback((topic) => send('POST', { topic }), [send]);
  const stop = useCallback(() => send('DELETE'), [send]);

  return { state, error, busy, start, stop, setError };
}
