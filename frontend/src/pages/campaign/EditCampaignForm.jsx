// The head person edits the campaign's brand, title, brief, prize or deadline. Only the fields
// that were changed are sent, so an old deadline never blocks a fix to the title.
import { useState } from 'react';
import { api } from '../../api.js';
import CampaignFields from '../../components/CampaignFields.jsx';
import { Alert, Button } from '../../components/ui.jsx';
import { changesFrom, formFromCampaign, problems } from '../../lib/campaignForm.js';

export default function EditCampaignForm({ campaign, hasScores, onSaved, onCancel }) {
  const [form, setForm] = useState(() => formFromCampaign(campaign));
  const [errors, setErrors] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const changes = changesFrom(campaign, form);

  const update = (key) => (event) => {
    setForm((current) => ({ ...current, [key]: event.target.value }));
    if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
  };

  async function handleSubmit(event) {
    event.preventDefault();
    setError('');
    const found = problems(form, { checkDeadline: 'deadline' in changes });
    setErrors(found);
    if (Object.keys(found).length) return;
    if (!Object.keys(changes).length) {
      setError('Nothing has changed yet.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api(`/api/campaigns/${campaign.id}`, { method: 'PATCH', body: changes });
      await onSaved();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="manage-edit stack" onSubmit={handleSubmit} noValidate aria-label="Edit campaign">
      <Alert tone="error">{error}</Alert>
      {campaign.status === 'winner_selected' ? (
        <Alert tone="info">A winner is already picked. Your changes show for everyone straight away, and the winner stays.</Alert>
      ) : null}
      <CampaignFields form={form} errors={errors} update={update} />
      {hasScores && 'brief' in changes ? (
        <Alert tone="info">You changed the brief. Use “Re-score all” afterwards so the AI scores match the new brief.</Alert>
      ) : null}
      <div className="form-actions">
        <Button type="submit" loading={busy}>Save changes</Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </form>
  );
}
