import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { Zap, DollarSign } from 'lucide-react';
import { ApprovalBell } from './ApprovalBell';
import { useAppStore } from '../lib/store';
import { fetchEnergy, fetchManagedAgents } from '../lib/api';
import { startStatusPolling } from '../lib/status-poll';

export const NAV_ITEMS = [
  { path: '/', label: 'Chat' },
  { path: '/dashboard', label: 'Dashboard' },
  { path: '/data-sources', label: 'Data sources' },
  { path: '/agents', label: 'Agents' },
  { path: '/logs', label: 'Logs' },
  { path: '/settings', label: 'Settings' },
  { path: '/get-started', label: 'Get started' },
] as const;

/** The Seneca mark: a rounded square holding two bars (pause, think). */
export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="1.25" y="1.25" width="17.5" height="17.5" rx="5.5" stroke="currentColor" strokeWidth="1.5" />
      <rect x="7" y="6" width="1.6" height="8" rx="0.8" fill="currentColor" />
      <rect x="11.4" y="6" width="1.6" height="8" rx="0.8" fill="currentColor" />
    </svg>
  );
}

function formatEnergy(joules: number): { value: string; unit: string } {
  if (joules >= 1e6) return { value: (joules / 1e6).toFixed(2), unit: 'MJ' };
  if (joules >= 1e3) return { value: (joules / 1e3).toFixed(1), unit: 'kJ' };
  return { value: joules.toFixed(0), unit: 'J' };
}

type Status = 'offline' | 'checking' | 'online' | 'generating' | 'agent';

const STATUS: Record<Status, { label: string; color: string }> = {
  offline: { label: 'Offline', color: 'var(--color-error)' },
  checking: { label: 'Connecting', color: 'var(--color-text-tertiary)' },
  online: { label: 'Online', color: 'var(--color-success)' },
  generating: { label: 'Generating', color: 'var(--color-accent)' },
  agent: { label: 'Agent running', color: 'var(--color-accent)' },
};

export function TopBar({ apiReachable }: { apiReachable: boolean | null }) {
  const navigate = useNavigate();
  const location = useLocation();
  const savings = useAppStore((s) => s.savings);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const [energyJ, setEnergyJ] = useState<number | null>(null);
  const [hasRunningAgent, setHasRunningAgent] = useState(false);

  // Energy counter and running agent state, polled quietly every 30 s. Started
  // once per reachable backend; savings updates must not restart it.
  useEffect(() => {
    if (apiReachable === false) return;
    return startStatusPolling({
      fetchEnergy,
      fetchManagedAgents,
      onEnergy: setEnergyJ,
      onRunningAgent: setHasRunningAgent,
    });
  }, [apiReachable]);

  const saved = savings
    ? Math.max(0, ...savings.per_provider.map((p) => p.total_cost - savings.local_cost))
    : null;
  const energy = energyJ != null ? formatEnergy(energyJ) : null;

  let status: Status = apiReachable === null ? 'checking' : apiReachable ? 'online' : 'offline';
  if (apiReachable && isStreaming) status = 'generating';
  if (apiReachable && hasRunningAgent) status = 'agent';
  const st = STATUS[status];

  return (
    <header
      className="flex items-center gap-4 px-4 md:px-6 shrink-0 relative z-30"
      style={{
        height: 60,
        background: 'var(--color-chrome)',
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      <button
        onClick={() => navigate('/')}
        className="flex items-center gap-2.5 shrink-0 cursor-pointer"
        style={{ color: 'var(--color-text)' }}
        aria-label="Seneca, go to chat"
      >
        <BrandMark />
        <span className="text-[15px] font-normal tracking-[-0.01em] hidden sm:inline">Seneca</span>
      </button>

      <nav className="flex items-center gap-1 min-w-0 overflow-x-auto ml-2 md:ml-6" aria-label="Main">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname === item.path;
          return (
            <button
              key={item.path}
              onClick={() => navigate(item.path)}
              className="sn-tab"
              data-active={active}
              aria-current={active ? 'page' : undefined}
            >
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-4 shrink-0">
        <div className="hidden lg:flex items-center gap-4 text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
          {energy && (
            <span className="flex items-center gap-1.5 tabular-nums" title="Energy used by local inference">
              <Zap size={13} strokeWidth={1.5} />
              <span style={{ color: 'var(--color-text)' }}>{energy.value}</span>
              {energy.unit}
            </span>
          )}
          {saved != null && saved > 0 && (
            <span className="flex items-center gap-1 tabular-nums" title="Saved against the most expensive cloud model">
              <DollarSign size={13} strokeWidth={1.5} />
              <span style={{ color: 'var(--color-text)' }}>{saved.toFixed(2)}</span>
              saved
            </span>
          )}
          {(energy || (saved != null && saved > 0)) && (
            <span className="w-px h-4" style={{ background: 'var(--color-border)' }} />
          )}
        </div>
        <span className="sn-pill" style={{ height: 22, fontSize: 12 }} role="status">
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: st.color }} />
          {st.label}
        </span>
        {serverInfo?.engine && (
          <span className="hidden md:inline text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>
            {serverInfo.engine}
          </span>
        )}
        <ApprovalBell />
      </div>
    </header>
  );
}
