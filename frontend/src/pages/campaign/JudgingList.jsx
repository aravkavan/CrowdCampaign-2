// The head person's list of ideas under the brief: the AI-ranked shortlist, then everything else.
// Buttons that change things (score, pick, undo, delete) live in ManageBox, not here.
import { EmptyState, PageLoader } from '../../components/ui.jsx';
import { plural } from '../../lib/format.js';
import { Entry, IdeaRow } from './IdeaEntries.jsx';

export default function JudgingList({ campaign, judging }) {
  const { ideas, error } = judging;
  const list = ideas ?? [];
  const shortlist = list.filter((s) => s.shortlisted);
  const others = list.filter((s) => !s.shortlisted);
  const canPick = campaign.status !== 'winner_selected';
  const pick = (idea) => judging.openPick(idea.id);

  return (
    <section className="judging" aria-labelledby="judging-heading">
      <div className="judging-head">
        <div>
          <h2 id="judging-heading" className="section-title">Judging</h2>
          <p className="muted">
            The judge scores each idea on creativity, relevance, feasibility and marketing potential, then ranks a top ten.
            It never picks the winner. You do.
          </p>
        </div>
      </div>

      {!ideas && !error ? <PageLoader label="Loading ideas" /> : null}
      {ideas && !list.length ? (
        <EmptyState title="No ideas yet">
          People will find this campaign on the Campaigns page. Their ideas appear here as they come in.
        </EmptyState>
      ) : null}

      {shortlist.length ? (
        <>
          <h3 className="list-heading">
            Shortlist <span className="list-heading-sub">top {shortlist.length} of {plural(list.length, 'idea')}, ranked by overall score</span>
          </h3>
          <ol className="leaderboard">
            {shortlist.map((idea) => (
              <Entry key={idea.id} idea={idea} canPick={canPick} onPick={() => pick(idea)} />
            ))}
          </ol>
        </>
      ) : null}

      {ideas && list.length && !shortlist.length ? (
        <p className="muted">No scores yet. Run the judge for a ranked shortlist, or read every idea below and pick a winner yourself.</p>
      ) : null}

      {others.length ? (
        <details className="more-ideas" open={!shortlist.length}>
          <summary>{shortlist.length ? `Everything else (${others.length})` : `All ideas (${others.length})`}</summary>
          <ul className="idea-list">
            {others.map((idea) => (
              <IdeaRow key={idea.id} idea={idea} canPick={canPick} onPick={() => pick(idea)} />
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
