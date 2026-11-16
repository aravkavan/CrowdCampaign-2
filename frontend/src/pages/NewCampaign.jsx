import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api.js';
import CampaignFields from '../components/CampaignFields.jsx';
import CampaignRow from '../components/CampaignRow.jsx';
import { Alert, Button } from '../components/ui.jsx';
import { defaultDeadline, problems } from '../lib/campaignForm.js';

export default function NewCampaign() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ brand: '', title: '', brief: '', prize: '', deadline: defaultDeadline() });
  const [errors, setErrors] = useState({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const update = (key) => (event) => {
    setForm((current) => ({ ...current, [key]: event.target.value }));
    if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
  };

  async function handleSubmit(event) {
    event.preventDefault();
    const found = problems(form);
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setError('');
    try {
      const created = await api('/api/campaigns', {
        method: 'POST',
        body: {
          brand: form.brand.trim(),
          title: form.title.trim(),
          brief: form.brief.trim(),
          prize: form.prize.trim(),
          deadline: new Date(form.deadline).toISOString(),
        },
      });
      navigate(`/campaigns/${created.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const preview = {
    id: 'preview',
    brand: form.brand.trim() || 'Your brand',
    title: form.title.trim() || 'Your campaign title',
    brief: form.brief.trim() || 'Your brief appears here: who you want to reach, what you want them to do, the budget, and anything off-limits.',
    prize: form.prize.trim() || 'Your prize',
    deadline: new Date(form.deadline).toString() === 'Invalid Date' ? new Date() : new Date(form.deadline),
    status: 'open',
    submission_count: 0,
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="display">Launch a campaign</h1>
          <p className="lede">Write the brief people will pitch against. You’ll see every idea, get an AI-ranked shortlist, and pick the winner yourself.</p>
        </div>
      </header>

      <div className="compose">
        <form className="panel stack" onSubmit={handleSubmit} noValidate>
          <Alert tone="error">{error}</Alert>
          <CampaignFields form={form} errors={errors} update={update} />
          <div className="form-actions">
            <Button type="submit" loading={busy}>
              Launch campaign
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </form>

        <aside className="compose-preview" aria-label="Preview">
          <p className="compose-preview-label">How it will look in the list</p>
          <div className="sheet sheet-preview" inert>
            <CampaignRow campaign={preview} />
          </div>
        </aside>
      </div>
    </div>
  );
}
