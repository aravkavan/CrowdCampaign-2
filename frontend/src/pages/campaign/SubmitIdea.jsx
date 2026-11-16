// The form a participant uses to send in their one idea.
import { useState } from 'react';
import { api } from '../../api.js';
import { Alert, Button, Field } from '../../components/ui.jsx';

export default function SubmitIdea({ campaign, onSubmitted }) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [errors, setErrors] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    const found = {};
    if (title.trim().length < 3) found.title = 'Give your idea a title (at least 3 characters).';
    if (content.trim().length < 30) found.content = 'Describe your idea in at least 30 characters.';
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setError('');
    try {
      await api(`/api/campaigns/${campaign.id}/submissions`, { method: 'POST', body: { title: title.trim(), content: content.trim() } });
      await onSubmitted();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="pitch-heading">
      <h2 id="pitch-heading" className="panel-title">Pitch your idea</h2>
      <p className="muted">
        One idea per person. Say what happens, where, why it fits the brief, and roughly what it costs. Only the organizer sees
        submitted ideas.
      </p>
      <form className="stack" onSubmit={handleSubmit} noValidate>
        <Alert tone="error">{error}</Alert>
        <Field label="Idea title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} error={errors.title} />
        <Field
          as="textarea"
          rows={7}
          label="Your idea"
          value={content}
          maxLength={5000}
          onChange={(e) => setContent(e.target.value)}
          error={errors.content}
          hint={`${content.trim().length} / 5000 characters`}
        />
        <div className="form-actions">
          <Button type="submit" loading={busy}>Submit idea</Button>
        </div>
      </form>
    </section>
  );
}
