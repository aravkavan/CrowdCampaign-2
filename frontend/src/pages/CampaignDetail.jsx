import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api.js';
import { Alert, EmptyState, PageLoader, StatusBadge } from '../components/ui.jsx';
import MarketIntelBox from '../components/MarketIntelBox.jsx';
import { formatDateTime, timeLeft } from '../lib/format.js';
import JudgingList from './campaign/JudgingList.jsx';
import AdminResearchBox from './campaign/AdminResearchBox.jsx';
import ManageBox from './campaign/ManageBox.jsx';
import ParticipantPanel from './campaign/ParticipantPanel.jsx';
import useJudging from './campaign/useJudging.js';
import WinnerBanner from './campaign/WinnerBanner.jsx';

export default function CampaignDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState(null);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState(false);
  const [intelVersion, setIntelVersion] = useState(0); // goes up when a research run finishes, so Market intel reloads

  const load = useCallback(async () => {
    try {
      setCampaign(await api(`/api/campaigns/${id}`));
      setError('');
    } catch (err) {
      if (err.status === 404) setMissing(true);
      else setError(err.message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // The head person's data (ideas, judge). Passing null means "not the head person": nothing is fetched.
  const judging = useJudging(campaign?.is_organizer ? campaign.id : null);

  if (missing) {
    return (
      <div className="page">
        <EmptyState title="This campaign doesn’t exist" action={<Link to="/campaigns" className="btn btn-primary">Browse campaigns</Link>}>
          It may have been deleted by its organizer.
        </EmptyState>
      </div>
    );
  }
  if (!campaign) {
    return <div className="page">{error ? <Alert tone="error">{error}</Alert> : <PageLoader label="Loading the brief" />}</div>;
  }

  // The head person, or a site admin (who may run the agent team on every campaign). An older server
  // without the flag: only the head person.
  const canResearch = campaign.can_research ?? campaign.is_organizer;

  return (
    <div className="page">
      <Link to="/campaigns" className="back-link">All campaigns</Link>
      <header className="campaign-head">
        <p className="campaign-brand">{campaign.brand}</p>
        <h1 className="display campaign-title">{campaign.title}</h1>
      </header>

      {campaign.is_organizer ? (
        <ManageBox
          campaign={campaign}
          judging={judging}
          onChanged={load}
          onDeleted={() => navigate('/campaigns', { replace: true })}
          onResearched={() => setIntelVersion((version) => version + 1)}
        />
      ) : canResearch ? (
        <AdminResearchBox campaign={campaign} onResearched={() => setIntelVersion((version) => version + 1)} />
      ) : null}

      <div className="campaign-grid">
        <article className="brief-doc" aria-label="The brief">
          <h2 className="brief-doc-title">The brief</h2>
          <p className="brief-doc-text">{campaign.brief}</p>
        </article>
        <aside className="facts" aria-label="Campaign details">
          <dl>
            <div className="fact">
              <dt>Prize</dt>
              <dd className="fact-prize"><span className="marker">{campaign.prize}</span></dd>
            </div>
            <div className="fact">
              <dt>Status</dt>
              <dd><StatusBadge status={campaign.status} /></dd>
            </div>
            <div className="fact">
              <dt>Deadline</dt>
              <dd>
                {formatDateTime(campaign.deadline)}
                {campaign.status === 'open' ? <span className="fact-sub">{timeLeft(campaign.deadline)}</span> : null}
              </dd>
            </div>
            <div className="fact">
              <dt>Ideas so far</dt>
              <dd>{campaign.submission_count}</dd>
            </div>
            <div className="fact">
              <dt>Organizer</dt>
              <dd>@{campaign.organizer.username}{campaign.is_organizer ? ' (you)' : ''}</dd>
            </div>
          </dl>
        </aside>
      </div>

      {campaign.winner ? <WinnerBanner winner={campaign.winner} campaign={campaign} /> : null}

      <MarketIntelBox campaignId={campaign.id} reloadKey={intelVersion} canResearch={canResearch} isOrganizer={campaign.is_organizer} />

      {campaign.is_organizer ? (
        <JudgingList campaign={campaign} judging={judging} />
      ) : (
        <ParticipantPanel campaign={campaign} onSubmitted={load} />
      )}
    </div>
  );
}
