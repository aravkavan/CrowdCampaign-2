// The one place a winner is chosen. It starts on the idea whose "Pick as winner" button was
// pressed, or on nothing at all when opened from the Manage box: the person chooses, not the AI.
import { useState } from 'react';
import { ConfirmDialog, Field } from '../../components/ui.jsx';

const label = (idea) =>
  `${idea.rank ? `#${idea.rank} ` : ''}${idea.title} (@${idea.author.username}${idea.evaluation ? `, ${idea.evaluation.overall.toFixed(1)}` : ', not scored'})`;

export default function PickWinnerDialog({ ideas, startId, busy, onConfirm, onCancel }) {
  const open = startId !== null;
  const [chosenId, setChosenId] = useState('');
  // Each time the dialog opens, start again from startId. Done while rendering (not in an effect)
  // so the previous choice is never shown, not even for one frame.
  const [seenStart, setSeenStart] = useState(null);
  if (startId !== seenStart) {
    setSeenStart(startId);
    if (startId !== null) setChosenId(startId);
  }

  const chosen = ideas.find((idea) => idea.id === chosenId);
  return (
    <ConfirmDialog
      open={open}
      title="Pick the winner"
      confirmLabel="Pick winner"
      busy={busy}
      confirmDisabled={!chosen}
      onConfirm={() => onConfirm(chosen)}
      onCancel={onCancel}
    >
      <div className="stack">
        <Field as="select" label="Winning idea" value={chosenId} onChange={(event) => setChosenId(event.target.value)}>
          <option value="">Choose an idea…</option>
          {ideas.map((idea) => (
            <option key={idea.id} value={idea.id}>{label(idea)}</option>
          ))}
        </Field>
        <p className="muted small">
          The campaign closes and everyone can see the winning idea. You can undo this later from the Manage box, and pick again.
        </p>
      </div>
    </ConfirmDialog>
  );
}
