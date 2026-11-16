// The small window the head person sees after pressing "Research this campaign": the topic to research
// (filled in from the brief, and theirs to change) and a plain note about what a run does and costs.
import { useState } from 'react';
import { ConfirmDialog, Field } from '../../components/ui.jsx';

const MIN = 10; // the same limits the server and the researcher use
const MAX = 300;

export default function ResearchDialog({ open, suggestion, busy, onConfirm, onCancel }) {
  const [topic, setTopic] = useState(suggestion);
  // Each time the window opens it starts again from the suggestion (reset while drawing, like PickWinnerDialog).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setTopic(suggestion);
  }

  const length = topic.replace(/\s+/g, ' ').trim().length;
  const problem = length < MIN ? `The topic needs at least ${MIN} characters.` : length > MAX ? `The topic can be at most ${MAX} characters.` : '';

  return (
    <ConfirmDialog
      open={open}
      title="Research this campaign"
      confirmLabel="Start research"
      busy={busy}
      confirmDisabled={Boolean(problem)}
      onConfirm={() => onConfirm(topic)}
      onCancel={onCancel}
    >
      <p>
        The researcher searches the web for what is new around this topic. The verifier then opens each source itself and checks
        every claim against its page. A run takes a few minutes and uses your model and search keys.
      </p>
      <Field
        label="Research topic"
        as="textarea"
        rows={4}
        value={topic}
        onChange={(event) => setTopic(event.target.value)}
        error={length && problem ? problem : undefined}
        hint={`${length} of ${MAX} characters. Change it to steer the search.`}
      />
    </ConfirmDialog>
  );
}
