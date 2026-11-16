// What a participant (anyone who is not the organizer) sees under the brief.
import { EmptyState } from '../../components/ui.jsx';
import { formatDateTime } from '../../lib/format.js';
import SubmitIdea from './SubmitIdea.jsx';

export default function ParticipantPanel({ campaign, onSubmitted }) {
  const mine = campaign.my_submission;
  if (mine) {
    return (
      <section className="panel" aria-labelledby="mine-heading">
        <h2 id="mine-heading" className="panel-title">Your idea</h2>
        <h3 className="idea-title">{mine.title}</h3>
        <p className="idea-text">{mine.content}</p>
        <p className="muted small">
          Submitted {formatDateTime(mine.created_at)}.{' '}
          {campaign.status === 'open' && 'The organizer reviews ideas and picks a winner after the deadline.'}
          {campaign.status === 'judging' && 'The organizer is reviewing ideas now.'}
          {campaign.status === 'winner_selected' && !mine.is_winner && 'A different idea won this time. Thanks for pitching.'}
        </p>
      </section>
    );
  }
  // Once a winner is shown above, there is nothing more to say here.
  if (campaign.status === 'winner_selected') return null;
  if (campaign.status !== 'open') {
    return (
      <section className="panel">
        <EmptyState title="This campaign has closed">
          The deadline passed and the organizer is choosing a winner.
        </EmptyState>
      </section>
    );
  }
  return <SubmitIdea campaign={campaign} onSubmitted={onSubmitted} />;
}
