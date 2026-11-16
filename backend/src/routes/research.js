// "Research this campaign": start, watch and stop the agent team for one campaign.
// Mounted at /api/campaigns, next to the campaign routes. Every route needs a login AND either the campaign's
// head person or a site admin (RESEARCH_ADMINS, default NYUgrader; admins may do this on every campaign).
// Nobody else can start, see or stop a run (403), and the server checks that, not just the button.
// The result is always saved under the head person's account, so everyone who opens the campaign sees it.
//
//   GET    /api/campaigns/:id/research   the state: can a run start, what is running, how the last one ended
//   POST   /api/campaigns/:id/research   { topic? } start the team  -> 202 with the state
//   DELETE /api/campaigns/:id/research   stop the run that is going for this campaign
import { Router } from 'express';
import { requireAuth } from '../auth/requireAuth.js';
import { HttpError, bodyOf } from '../lib/http.js';
import { canResearch, describeResearch, readTopic, startResearch, stopResearch } from '../services/research.js';
import { loadCampaign } from './campaigns.js';

export const researchRouter = Router();

async function researchCampaign(req) {
  const campaign = await loadCampaign(req.params.id, req.user.id);
  if (!canResearch(campaign, req.user)) throw new HttpError(403, 'Only this campaign’s head person or a site admin can do that.');
  return campaign;
}

researchRouter.get('/:id/research', requireAuth, async (req, res) => {
  const campaign = await researchCampaign(req);
  res.json(await describeResearch(campaign, req.user));
});

researchRouter.post('/:id/research', requireAuth, async (req, res) => {
  const campaign = await researchCampaign(req);
  const topic = readTopic(bodyOf(req).topic, campaign);
  try {
    await startResearch({ campaign, user: req.user, topic });
  } catch (err) {
    if (err.retryAfterSeconds) res.set('Retry-After', String(err.retryAfterSeconds));
    throw err;
  }
  res.status(202).json(await describeResearch(campaign, req.user));
});

researchRouter.delete('/:id/research', requireAuth, async (req, res) => {
  const campaign = await researchCampaign(req);
  stopResearch(campaign);
  res.json(await describeResearch(campaign, req.user));
});
