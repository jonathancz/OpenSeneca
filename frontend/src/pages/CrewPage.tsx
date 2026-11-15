import { useEffect, useState } from 'react';
import { CrewBoard } from '../components/Crew/CrewBoard';
import { useCrewStore } from '../lib/crew-api';
import '../components/Crew/crew.css';

/**
 * Crew: what the coding crew is doing, read only from firstmate's records.
 * The fleet itself is kept fresh by the live subscription in Layout, so the
 * tab count stays right from any page; this page only re-renders ages.
 */
export function CrewPage() {
  const response = useCrewStore((s) => s.response);
  const error = useCrewStore((s) => s.error);
  const loadedAt = useCrewStore((s) => s.loadedAt);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  if (!response) {
    return (
      <div className="cw-page">
        <div className="cw-notice">{error ? `Cannot read the crew right now (${error}).` : 'Reading the crew…'}</div>
      </div>
    );
  }
  if (!response.available || !response.fleet) {
    return (
      <div className="cw-page">
        <div className="cw-notice">
          The crew feed has not written anything yet. Start the feeder on this machine with{' '}
          <code>scripts/cockpit-feed/run.sh</code>; see <code>scripts/cockpit-feed/README.md</code>.
        </div>
      </div>
    );
  }
  // The server's age was measured when the fleet was fetched; keep counting.
  const sinceFetch = loadedAt ? Math.max(0, Math.floor((nowMs - loadedAt) / 1000)) : 0;
  const ageSeconds = response.age_seconds != null ? response.age_seconds + sinceFetch : null;
  return (
    <div className="cw-page">
      <CrewBoard
        fleet={response.fleet}
        nowMs={nowMs}
        ageSeconds={ageSeconds}
        stale={Boolean(response.stale) || (ageSeconds ?? 0) > 300}
      />
    </div>
  );
}
