// "Research this campaign": the head person (or a site admin) starts the agent team (researcher + verifier +
// orchestrator) for this campaign, watches what it is doing, can stop it, and sees how the last run ended.
// It sits inside the Manage box for the head person, and inside the Site admin box for an admin.
// Everything the team wrote (progress lines, the last words of a failed run) is shown as plain text, never as HTML.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button } from '../../components/ui.jsx';
import { plural, workersText } from '../../lib/format.js';
import ResearchDialog from './ResearchDialog.jsx';
import useResearch from './useResearch.js';

function LastRun({ run, campaign }) {
  if (run.status === 'running') return null;
  const result = run.result;
  if (run.status === 'done') {
    const partial = result && result.status !== 'complete';
    const failed = result?.failed_workers ?? [];
    return (
      <>
        <Alert tone={partial ? 'info' : 'success'}>
          {result
            ? `Run ${result.number} is ${result.status}${failed.length ? ` (${workersText(failed)} failed, so some claims are unchecked)` : ''}.`
            : 'The team finished.'}{' '}
          The result is under Market intel below, where everyone who opens this campaign can see it.{' '}
          {campaign.is_organizer ? (
            <>The full report is in <Link to="/intel">Market intel</Link>.</>
          ) : (
            `The full report is saved to @${campaign.organizer.username}, the head person.`
          )}
        </Alert>
        {run.message ? <p className="muted small manage-research-said">{run.message}</p> : null}
      </>
    );
  }
  if (run.status === 'failed') return <Alert tone="error">{`The team could not finish this run. ${run.message ?? ''}`}</Alert>;
  return <Alert tone="info">{run.message || 'The run was stopped.'}</Alert>;
}

export default function ResearchPanel({ campaign, onFinished }) {
  const { state, error, busy, start, stop } = useResearch(campaign.id, onFinished);
  const [dialogOpen, setDialogOpen] = useState(false);

  if (!state) return error ? <Alert tone="error">{error}</Alert> : null;
  const run = state.run;
  const running = run?.status === 'running';

  async function begin(topic) {
    await start(topic);
    setDialogOpen(false); // if it did not start, the reason is shown above the button
  }

  return (
    <div className="manage-research" aria-labelledby="research-heading">
      <h3 id="research-heading" className="manage-research-title">Market intel team</h3>
      <p className="muted small">
        Starts the researcher and the verifier for this campaign: it searches the web for what is new, then checks every claim
        against its page. Only the head person and site admins can start it. Everyone who opens the campaign sees the result.
      </p>

      <Alert tone="error">{error}</Alert>

      {!state.available ? <p className="muted small">{state.unavailable_reason}</p> : null}

      {running ? (
        <div className="manage-research-live">
          <p role="status" className="manage-research-working">
            <span className="spinner" aria-hidden="true" /> The team is working on “{run.topic}”. This takes a few minutes.
          </p>
          {run.progress.length ? (
            <ol className="manage-research-log" aria-label="What the team is doing">
              {run.progress.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ol>
          ) : null}
          <Button variant="secondary" size="small" onClick={stop} loading={busy}>Stop the run</Button>
        </div>
      ) : state.available ? (
        <div className="manage-actions">
          <Button variant="secondary" onClick={() => setDialogOpen(true)} disabled={!state.can_start}>Research this campaign</Button>
          <span className="muted small">
            {state.can_start
              ? `You can start ${plural(state.runs_left_today, 'more run')} today.`
              : state.blocked_reason}
          </span>
        </div>
      ) : null}

      {run ? <LastRun run={run} campaign={campaign} /> : null}

      <ResearchDialog
        open={dialogOpen}
        suggestion={state.topic_suggestion}
        busy={busy}
        onConfirm={begin}
        onCancel={() => setDialogOpen(false)}
      />
    </div>
  );
}
