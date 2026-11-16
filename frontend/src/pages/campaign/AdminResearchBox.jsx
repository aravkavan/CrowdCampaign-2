// What a site admin (RESEARCH_ADMINS, e.g. NYUgrader) sees on a campaign they did not launch: only the agent team.
// They cannot edit, score, pick or delete here; that stays with the head person. The result is saved to the head
// person's account, and everyone who opens the campaign sees it in the Market intel box.
import ResearchPanel from './ResearchPanel.jsx';

export default function AdminResearchBox({ campaign, onResearched }) {
  return (
    <section className="manage manage-admin" aria-labelledby="admin-research-heading">
      <div className="manage-head">
        <h2 id="admin-research-heading" className="manage-title">Site admin</h2>
        <p className="muted small">
          You can run Market intel on every campaign. Only @{campaign.organizer.username} can edit or pick the winner.
        </p>
      </div>
      <ResearchPanel campaign={campaign} onFinished={onResearched} />
    </section>
  );
}
