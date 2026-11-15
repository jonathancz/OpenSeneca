import { useEffect, useMemo, useState } from 'react';
import { MessageCircle, X } from 'lucide-react';
import { CrewBoard } from '../components/Crew/CrewBoard';
import { AgentView } from '../components/Crew/AgentView';
import { SenecaDock } from '../components/Crew/SenecaDock';
import { StateGlyph } from '../components/Crew/parts';
import { fetchNudges, useCrewStore } from '../lib/crew-api';
import { type NudgeStatus, nudgeLine } from '../lib/crew';
import { BOARD, type CrewTabs, closeTab, initialTabs, isDockShortcut, openTab, pruneTabs } from '../lib/crew-tabs';
import { modKey } from '../components/HintBar';
import '../components/Crew/crew.css';

function wideScreen(): boolean {
  try {
    return window.matchMedia('(min-width: 1101px)').matches;
  } catch {
    return true;
  }
}

/**
 * Crew: what the coding crew is doing, read only from firstmate's records.
 * The fleet itself is kept fresh by the live subscription in Layout, so the
 * tab count stays right from any page; this page re-renders ages, opens
 * agents as tabs and docks Seneca's chat on the right.
 */
export function CrewPage() {
  const response = useCrewStore((s) => s.response);
  const error = useCrewStore((s) => s.error);
  const loadedAt = useCrewStore((s) => s.loadedAt);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [tabs, setTabs] = useState<CrewTabs>(initialTabs);
  const [dock, setDock] = useState(wideScreen);
  const [nudges, setNudges] = useState<NudgeStatus | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isDockShortcut(e)) return;
      e.preventDefault();
      setDock((d) => !d);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const fleet = response?.available ? response.fleet : undefined;
  const version = fleet?.version;

  // Nudge status changes rarely; read it with each new fleet.
  useEffect(() => {
    if (version == null) return;
    let cancelled = false;
    fetchNudges()
      .then((n) => !cancelled && setNudges(n))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [version]);

  const agentIds = useMemo(() => new Set(fleet?.agents.map((a) => a.id) ?? []), [fleet]);
  useEffect(() => {
    if (fleet) setTabs((t) => pruneTabs(t, agentIds));
  }, [fleet, agentIds]);

  if (!response) {
    return (
      <div className="cw-page">
        <div className="cw-notice">{error ? `Cannot read the crew right now (${error}).` : 'Reading the crew…'}</div>
      </div>
    );
  }
  if (!fleet) {
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
  const current = tabs.current === BOARD ? null : fleet.agents.find((a) => a.id === tabs.current) ?? null;

  return (
    <div className="cw-shell" data-dock={dock ? 'open' : 'closed'}>
      <div className="cw-main">
        <div className="cw-tabrail" role="tablist" aria-label="Crew tabs">
          <button
            className="sn-tab"
            role="tab"
            data-active={tabs.current === BOARD}
            aria-selected={tabs.current === BOARD}
            onClick={() => setTabs((t) => ({ ...t, current: BOARD }))}
          >
            Board
          </button>
          {tabs.open.map((id) => {
            const a = fleet.agents.find((x) => x.id === id);
            if (!a) return null;
            return (
              <button
                key={id}
                className="sn-tab"
                role="tab"
                data-active={tabs.current === id}
                aria-selected={tabs.current === id}
                onClick={() => setTabs((t) => openTab(t, id))}
              >
                <StateGlyph state={a.state} />
                <span className="cw-tab-title">{a.title}</span>
                <span
                  className="cw-tab-x"
                  role="button"
                  aria-label={`Close ${a.title}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setTabs((t) => closeTab(t, id));
                  }}
                >
                  <X size={12} strokeWidth={1.8} />
                </span>
              </button>
            );
          })}
          <span className="cw-rail-right">
            <button
              className="cw-ghost"
              aria-pressed={dock}
              onClick={() => setDock((d) => !d)}
              title={`Show or hide Seneca (${modKey('J')})`}
            >
              <MessageCircle size={14} strokeWidth={1.6} />
              Seneca
            </button>
          </span>
        </div>
        {current ? (
          <AgentView key={current.id} agent={current} fleet={fleet} nowMs={nowMs} />
        ) : (
          <div className="cw-page">
            <CrewBoard
              fleet={fleet}
              nowMs={nowMs}
              ageSeconds={ageSeconds}
              stale={Boolean(response.stale) || (ageSeconds ?? 0) > 300}
              nudgeLine={nudgeLine(nudges)}
              onOpen={(id) => setTabs((t) => openTab(t, id))}
            />
          </div>
        )}
      </div>
      {dock && <SenecaDock nudges={nudges} />}
    </div>
  );
}
