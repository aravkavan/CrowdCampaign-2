// The campaign form's rules, shared by "Launch a campaign" and "Edit campaign".
// The server checks the same limits again (backend/src/lib/campaignFields.js); this copy only
// gives quick feedback before anything is sent. If a limit changes, change both files.

// <input type="datetime-local"> works in local time, formatted as YYYY-MM-DDTHH:mm.
export function toLocalInput(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function defaultDeadline() {
  const date = new Date(Date.now() + 14 * 24 * 3600 * 1000);
  date.setHours(17, 0, 0, 0);
  return toLocalInput(date);
}

export const FIELD_KEYS = ['brand', 'title', 'brief', 'prize', 'deadline'];
export const LIMITS = { brand: [2, 80], title: [5, 120], brief: [40, 4000], prize: [2, 120] };
export const LABELS = { brand: 'Brand name', title: 'Title', brief: 'Brief', prize: 'Prize' };

// { field: message } for everything wrong in `form`. Empty object means all good.
// checkDeadline=false skips the "must be in the future" rule: when editing, a deadline that has
// already passed and was left alone must not block a fix to the title.
export function problems(form, { checkDeadline = true } = {}) {
  const found = {};
  for (const [key, [min, max]] of Object.entries(LIMITS)) {
    const length = form[key].trim().length;
    if (length < min) found[key] = `${LABELS[key]} needs at least ${min} characters.`;
    else if (length > max) found[key] = `${LABELS[key]} can be at most ${max} characters.`;
  }
  if (checkDeadline) {
    const deadline = new Date(form.deadline);
    if (Number.isNaN(deadline.getTime())) found.deadline = 'Pick a date and time.';
    else if (deadline.getTime() <= Date.now()) found.deadline = 'Pick a time in the future.';
  }
  return found;
}

// A campaign as the server sent it -> the strings the form fields hold.
export function formFromCampaign(campaign) {
  return {
    brand: campaign.brand,
    title: campaign.title,
    brief: campaign.brief,
    prize: campaign.prize,
    deadline: toLocalInput(new Date(campaign.deadline)),
  };
}

// The request body for editing: only the fields that differ from `campaign`, so an edit never
// re-sends (or re-checks) something the person did not touch.
export function changesFrom(campaign, form) {
  const body = {};
  for (const key of ['brand', 'title', 'brief', 'prize']) {
    if (form[key].trim() !== campaign[key]) body[key] = form[key].trim();
  }
  if (form.deadline !== toLocalInput(new Date(campaign.deadline))) body.deadline = new Date(form.deadline).toISOString();
  return body;
}
