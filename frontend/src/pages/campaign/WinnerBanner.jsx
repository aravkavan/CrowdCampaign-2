// The big banner shown to everyone once the organizer has picked a winner.
import { useAuth } from '../../auth.jsx';

export default function WinnerBanner({ winner, campaign }) {
  const { user } = useAuth();
  const yours = winner.author.id === user.id;
  return (
    <section className="winner" aria-labelledby="winner-heading">
      <p className="winner-label" id="winner-heading">
        {yours ? 'Your idea won this campaign' : `Winning idea, picked by @${campaign.organizer.username}`}
      </p>
      <h2 className="winner-title"><span className="marker">{winner.title}</span></h2>
      <p className="winner-author">by @{winner.author.username}</p>
      <p className="winner-text">{winner.content}</p>
    </section>
  );
}
