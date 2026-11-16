// The head person's control box, at the top of the campaign page: edit, run the AI score,
// pick the winner, undo the pick, delete. Everything here is organizer-only (the server
// refuses anyone else too). It gets its data from useJudging and never fetches ideas itself.
import { useState } from 'react';
import { api } from '../../api.js';
import { Alert, Button, ConfirmDialog } from '../../components/ui.jsx';
import { plural } from '../../lib/format.js';
import EditCampaignForm from './EditCampaignForm.jsx';
import PickWinnerDialog from './PickWinnerDialog.jsx';
import ResearchPanel from './ResearchPanel.jsx';

export default function ManageBox({ campaign, judging, onChanged, onDeleted, onResearched }) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [undoOpen, setUndoOpen] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const { ideas, judge, scoring, error, message, setError, setMessage } = judging;
  const list = ideas ?? [];
  const unscored = list.filter((idea) => !idea.evaluation).length;
  const decided = campaign.status === 'winner_selected';

  function clearNotes() {
    setError('');
    setMessage('');
  }

  async function pickWinner(idea) {
    setPicking(true);
    clearNotes();
    try {
      await api(`/api/campaigns/${campaign.id}/winner`, { method: 'POST', body: { submission_id: idea.id } });
      judging.closePick();
      await Promise.all([onChanged(), judging.reload()]);
      setMessage('Winner picked. If you change your mind, use “Undo winner pick” in this box.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      setError(err.message);
      judging.closePick();
    } finally {
      setPicking(false);
    }
  }

  async function undoPick() {
    setUndoing(true);
    clearNotes();
    try {
      await api(`/api/campaigns/${campaign.id}/winner`, { method: 'DELETE' });
      await Promise.all([onChanged(), judging.reload()]);
      setMessage('The winner pick is undone. Choose a winner again whenever you are ready.');
    } catch (err) {
      setError(err.message);
    } finally {
      setUndoing(false);
      setUndoOpen(false);
    }
  }

  async function deleteCampaign() {
    setDeleting(true);
    try {
      await api(`/api/campaigns/${campaign.id}`, { method: 'DELETE' });
      onDeleted();
    } catch (err) {
      setError(err.message);
      setDeleting(false);
      setDeleteOpen(false);
    }
  }

  async function saved() {
    await onChanged();
    setEditing(false);
    clearNotes();
    setMessage('Saved. Everyone now sees the updated campaign.');
  }

  return (
    <section className="manage" aria-labelledby="manage-heading">
      <div className="manage-head">
        <h2 id="manage-heading" className="manage-title">Manage this campaign</h2>
        <p className="muted small">Only you can see this box. {decided ? 'A winner is picked.' : 'Nobody has won yet.'}</p>
      </div>

      <Alert tone="error">{error}</Alert>
      <Alert tone="success">{message}</Alert>

      {editing ? (
        <EditCampaignForm campaign={campaign} hasScores={list.some((idea) => idea.evaluation)} onSaved={saved} onCancel={() => setEditing(false)} />
      ) : (
        <div className="manage-actions">
          <Button variant="secondary" onClick={() => { clearNotes(); setEditing(true); }}>Edit campaign</Button>
          {!decided && list.length ? (
            <>
              <Button variant="secondary" onClick={() => judging.score(false)} loading={scoring} disabled={!unscored}>
                {unscored ? `Score ${plural(unscored, 'new idea')}` : 'All ideas scored'}
              </Button>
              {list.length > unscored ? (
                <Button variant="ghost" onClick={() => judging.score(true)} disabled={scoring}>Re-score all</Button>
              ) : null}
              <Button onClick={() => { clearNotes(); judging.openPick(); }}>Pick winner</Button>
            </>
          ) : null}
          {decided ? <Button variant="secondary" onClick={() => setUndoOpen(true)}>Undo winner pick</Button> : null}
          <Button variant="danger-ghost" size="small" className="manage-delete" onClick={() => setDeleteOpen(true)}>Delete campaign</Button>
        </div>
      )}

      {judge ? (
        <p className={`judge-note ${judge.is_ai ? '' : 'judge-note-offline'}`}>
          Judge: <strong>{judge.label}</strong>
          {judge.is_ai ? null : '. Add an AI key to the backend to get real AI scores; everything else works the same.'}
        </p>
      ) : null}
      {scoring ? <p className="scoring-note" role="status">Scoring ideas. With an AI judge this can take up to a minute.</p> : null}

      <ResearchPanel campaign={campaign} onFinished={onResearched} />

      <PickWinnerDialog ideas={list} startId={judging.pickFor} busy={picking} onConfirm={pickWinner} onCancel={judging.closePick} />

      <ConfirmDialog
        open={undoOpen}
        title="Undo the winner pick?"
        confirmLabel="Undo pick"
        busy={undoing}
        onConfirm={undoPick}
        onCancel={() => setUndoOpen(false)}
      >
        <p>
          <strong>{campaign.winner?.title}</strong> will no longer be the winner. The campaign goes back to judging and you can pick
          again. Only you can do this.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete this campaign?"
        confirmLabel="Delete campaign"
        tone="danger"
        busy={deleting}
        onConfirm={deleteCampaign}
        onCancel={() => setDeleteOpen(false)}
      >
        <p>The brief and all {plural(campaign.submission_count, 'idea')} will be removed for good. This one cannot be undone.</p>
      </ConfirmDialog>
    </section>
  );
}
