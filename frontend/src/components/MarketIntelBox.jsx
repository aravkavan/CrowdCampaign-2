import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { formatDate, hostOf, safeHref, claimTotal, countClaims, workersText } from '../lib/format.js';
import { ClaimCounts } from './ClaimLabel.jsx';
import MoreText from './MoreText.jsx';

// Your tracker's latest top list for one campaign, shown on the campaign page.
// Each finding is one short line with "More" next to it; More shows the whole summary, the claim labels and the source.
// Everyone who opens the campaign sees it (the server sends the head person's latest list to every viewer), so
// people writing ideas know what is new. With no research yet it shows nothing, except to someone who can run the
// team (the head person or a site admin): they see the box with a button that takes them up to "Research this
// campaign". The Full report link is only for the head person, whose account the runs are saved under.
// Everything from the web (titles, summaries, claims) is rendered as React text, never as HTML.
// `reloadKey` changes when a run has just finished (started from the Manage box), so the list is fetched again.

// Scrolls to the team's part of the Manage box and puts the keyboard there.
function goToResearch() {
  const target = document.getElementById('research-heading') ?? document.getElementById('manage-heading');
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const button = target.parentElement?.querySelector('button');
  (button ?? target).focus?.({ preventScroll: true });
}

function RunButton({ children }) {
  return (
    <button type="button" className="text-button intel-run" onClick={goToResearch}>
      {children} <span aria-hidden="true">↑</span>
    </button>
  );
}

function Finding({ item }) {
  const d = item.development;
  const source = d.sources[0];
  const href = source ? safeHref(source.url) : null;
  const counts = countClaims(item.claims);
  return (
    <li className="intel-mini-item">
      <span className="intel-mini-rank">{item.rank}</span>
      <div className="intel-mini-body">
        <p className="intel-mini-title">
          {d.title}
          {item.change === 'new' ? <span className="tag-new">New</span> : null}
        </p>
        <MoreText text={d.summary} max={100} className="intel-mini-summary" label={`about ${d.title}`}>
          {claimTotal(counts) || source ? (
            <>
              {claimTotal(counts) ? (
                <p className="intel-mini-claims">
                  <ClaimCounts counts={counts} />
                </p>
              ) : null}
              {source ? (
                <p className="intel-mini-source">
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {hostOf(source.url)}
                    </a>
                  ) : (
                    <span>{source.url}</span>
                  )}
                  {d.sources.length > 1 ? ` and ${d.sources.length - 1} more` : ''}
                </p>
              ) : null}
            </>
          ) : null}
        </MoreText>
      </div>
    </li>
  );
}

export default function MarketIntelBox({ campaignId, reloadKey = 0, canResearch = false, isOrganizer = false }) {
  const [intel, setIntel] = useState(null);

  useEffect(() => {
    let alive = true;
    api(`/api/tracker/campaigns/${campaignId}`)
      .then((data) => alive && setIntel(data))
      .catch(() => alive && setIntel(null));
    return () => {
      alive = false;
    };
  }, [campaignId, reloadKey]);

  if (!intel?.top?.length) {
    if (!canResearch) return null;
    return (
      <section className="intel-box intel-box-empty" aria-labelledby="intel-box-heading">
        <div className="intel-box-head">
          <h2 id="intel-box-heading" className="panel-title">
            Market intel
          </h2>
        </div>
        <p className="muted small">
          No research for this campaign yet. When you run it, everyone who opens this campaign will see what is new.
        </p>
        <RunButton>Go to Research this campaign</RunButton>
      </section>
    );
  }
  // The newest run may have been unable to research this campaign; then the list shown is from an earlier run.
  const newer = intel.latest && intel.latest.run !== intel.run.number ? intel.latest : null;
  return (
    <section className="intel-box" aria-labelledby="intel-box-heading">
      <div className="intel-box-head">
        <h2 id="intel-box-heading" className="panel-title">
          Market intel
        </h2>
        <span className="intel-box-links">
          {canResearch ? <RunButton>Run the team again</RunButton> : null}
          {isOrganizer ? (
            <Link to="/intel" className="panel-link">
              Full report
            </Link>
          ) : null}
        </span>
      </div>
      <p className="muted small">
        What’s new around this brief, found by the research team on {formatDate(intel.run.finished_at)} (run {intel.run.number}).
        Build on these instead of repeating them. Press More for the details and the source.
      </p>
      {newer ? (
        <p className="run-note run-note-failed">
          <strong>Out of date.</strong> Run {newer.run} could not research this campaign
          {newer.failed_workers?.length ? ` (${workersText(newer.failed_workers)} failed)` : ''}, so this is the list from run{' '}
          {intel.run.number}.
        </p>
      ) : null}
      {intel.failed_workers?.includes('verifier') ? (
        <p className="run-note">
          <strong>Not fully checked.</strong> The verifier was not available for run {intel.run.number}, so claims it could not
          check are marked Unverified.
        </p>
      ) : null}
      <ol className="intel-mini">
        {intel.top.map((item) => (
          <Finding key={item.development.id} item={item} />
        ))}
      </ol>
    </section>
  );
}
