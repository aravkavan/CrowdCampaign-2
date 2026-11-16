import { CLAIM_ORDER, CLAIM_VERDICT } from '../lib/format.js';

// The label on one claim: Supported, Unsupported, Unreachable or Unverified. It is always words, with colour as
// a second signal. With `count` it reads like "2 unreachable", for a summary line.
export function ClaimLabel({ verdict, count }) {
  const word = CLAIM_VERDICT[verdict] ?? verdict;
  return <span className={`claim-label claim-label-${verdict}`}>{count === undefined ? word : `${count} ${word.toLowerCase()}`}</span>;
}

// A row of labels with their counts, zeros left out: "1 supported  2 unreachable".
export function ClaimCounts({ counts }) {
  const shown = CLAIM_ORDER.filter((verdict) => counts?.[verdict]);
  if (!shown.length) return null;
  return (
    <span className="claim-counts">
      {shown.map((verdict) => (
        <ClaimLabel key={verdict} verdict={verdict} count={counts[verdict]} />
      ))}
    </span>
  );
}
