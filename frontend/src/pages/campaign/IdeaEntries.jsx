// One idea in the organizer's lists: Entry is a shortlisted (ranked) idea, IdeaRow is any other.
import { useState } from 'react';
import { Button, ScoreBar } from '../../components/ui.jsx';
import { judgeName } from '../../lib/format.js';

export function Entry({ idea, canPick, onPick }) {
  const [open, setOpen] = useState(false);
  const e = idea.evaluation;
  return (
    <li className={`entry ${idea.is_winner ? 'entry-winner' : ''}`}>
      <span className="entry-rank" aria-label={`Rank ${idea.rank}`}>{idea.rank}</span>
      <div className="entry-body">
        <div className="entry-top">
          <div>
            <h4 className="entry-title">{idea.title}</h4>
            <p className="entry-author">@{idea.author.username}</p>
          </div>
          <p className="entry-overall">
            <span className="entry-overall-value">{e.overall.toFixed(1)}</span>
            <span className="entry-overall-label">overall</span>
          </p>
        </div>
        <div className="entry-scores">
          <ScoreBar label="Creativity" value={e.creativity} />
          <ScoreBar label="Relevance" value={e.relevance} />
          <ScoreBar label="Feasibility" value={e.feasibility} />
          <ScoreBar label="Marketing potential" value={e.marketing_potential} />
        </div>
        <p className="entry-summary">
          {e.summary} <span className="entry-judge">{judgeName(e.method)}</span>
        </p>
        {open ? <p className="entry-text">{idea.content}</p> : null}
        <div className="entry-actions">
          <button type="button" className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide the full idea' : 'Read the full idea'}
          </button>
          {idea.is_winner ? <span className="won-tag">Winner</span> : null}
          {!idea.is_winner && canPick ? (
            <Button variant="secondary" size="small" onClick={onPick}>
              Pick as winner
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function IdeaRow({ idea, canPick, onPick }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="idea-row">
      <div className="idea-row-top">
        <div>
          <p className="idea-row-title">{idea.title}</p>
          <p className="entry-author">
            @{idea.author.username}
            {idea.evaluation ? `, ranked ${idea.rank} with ${idea.evaluation.overall.toFixed(1)}` : ', not scored yet'}
          </p>
        </div>
        <div className="entry-actions">
          <button type="button" className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide' : 'Read'}
          </button>
          {idea.is_winner ? <span className="won-tag">Winner</span> : null}
          {!idea.is_winner && canPick ? (
            <Button variant="secondary" size="small" onClick={onPick}>
              Pick as winner
            </Button>
          ) : null}
        </div>
      </div>
      {open ? (
        <>
          <p className="entry-text">{idea.content}</p>
          {idea.evaluation ? <p className="entry-summary">{idea.evaluation.summary}</p> : null}
        </>
      ) : null}
    </li>
  );
}
