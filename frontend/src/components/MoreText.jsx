import { useId, useState } from 'react';
import { shortText } from '../lib/format.js';

// One short line with a "More" button next to it. "More" shows the whole text, and anything passed as
// `children` (claims, sources); "Less" folds it back. With nothing more to show, it is just the line.
// The text comes from the web, so it is rendered as React text, never as HTML.
export default function MoreText({ text, max = 110, className = '', children = null, label = 'details' }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const { short, cut } = shortText(text, max);
  const extra = Boolean(children);
  const canOpen = cut || extra;
  return (
    <>
      <p className={`more-text ${className}`}>
        {open || !cut ? String(text ?? '').trim() : short}
        {canOpen ? (
          <>
            {' '}
            <button
              type="button"
              className="more-toggle"
              aria-expanded={open}
              aria-controls={extra ? id : undefined}
              aria-label={open ? `Show less ${label}` : `Show more ${label}`}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? 'Less' : 'More'}
            </button>
          </>
        ) : null}
      </p>
      {extra && open ? (
        <div id={id} className="more-extra">
          {children}
        </div>
      ) : null}
    </>
  );
}
