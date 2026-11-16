// The five campaign fields (brand, title, brief, prize, deadline), used by the Launch page and
// the Edit form. `form` holds the strings, `errors` the messages, `update(key)` makes an onChange.
import { Field } from './ui.jsx';
import { toLocalInput } from '../lib/campaignForm.js';

export default function CampaignFields({ form, errors, update }) {
  return (
    <>
      <Field label="Brand or organization" value={form.brand} onChange={update('brand')} error={errors.brand} maxLength={80} placeholder="Sunny Sip" />
      <Field label="Campaign title" value={form.title} onChange={update('title')} error={errors.title} maxLength={120} placeholder="Get students talking about our new drink" />
      <Field
        as="textarea"
        label="The brief"
        rows={8}
        value={form.brief}
        onChange={update('brief')}
        error={errors.brief}
        maxLength={4000}
        hint={`Who it’s for, what you want them to do, budget, timeline, anything off-limits. ${form.brief.trim().length} / 4000`}
      />
      <div className="field-pair">
        <Field label="Prize" value={form.prize} onChange={update('prize')} error={errors.prize} maxLength={120} placeholder="$500 and a feature" />
        <Field label="Deadline" type="datetime-local" value={form.deadline} min={toLocalInput(new Date())} onChange={update('deadline')} error={errors.deadline} />
      </div>
    </>
  );
}
