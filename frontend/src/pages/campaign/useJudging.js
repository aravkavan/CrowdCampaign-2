// The head person's view of the ideas, kept in one place: the ranked list, which judge is
// active, scoring, the notices to show, and which idea the "Pick winner" dialog starts on.
// Pass null as the id (a participant is looking) and nothing is fetched.
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api.js';

export default function useJudging(campaignId) {
  const [ideas, setIdeas] = useState(null); // null until loaded
  const [judge, setJudge] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [scoring, setScoring] = useState(false);
  // null = the pick dialog is closed. '' = open with nothing chosen. Otherwise the id of the idea it starts on.
  const [pickFor, setPickFor] = useState(null);

  const reload = useCallback(async () => {
    if (!campaignId) return;
    const [list, status] = await Promise.all([api(`/api/campaigns/${campaignId}/submissions`), api('/api/ai/status')]);
    setIdeas(list.submissions);
    setJudge(status);
  }, [campaignId]);

  useEffect(() => {
    reload().catch((err) => setError(err.message));
  }, [reload]);

  // force=false scores only the ideas without a score; force=true scores everything again.
  async function score(force) {
    setScoring(true);
    setError('');
    setMessage('');
    try {
      const result = await api(`/api/campaigns/${campaignId}/evaluate`, { method: 'POST', body: { force } });
      setIdeas(result.submissions);
      setMessage(result.message);
    } catch (err) {
      setError(err.message);
    } finally {
      setScoring(false);
    }
  }

  return {
    ideas, judge, error, message, scoring,
    score, reload, setError, setMessage,
    pickFor, openPick: (ideaId = '') => setPickFor(ideaId), closePick: () => setPickFor(null),
  };
}
