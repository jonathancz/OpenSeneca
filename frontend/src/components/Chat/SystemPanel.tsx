import { useState, useEffect, useCallback } from 'react';
import { Cloud, HardDrive, X, Trophy, ExternalLink } from 'lucide-react';
import { Figure, Kbd, SectionHead } from '../ui/seneca';
import { modKey } from '../HintBar';
import { useAppStore } from '../../lib/store';
import { getBase } from '../../lib/api';

interface EnergyData {
  total_energy_j?: number;
  energy_per_token_j?: number;
  avg_power_w?: number;
  cpu_temp_c?: number | null;
  gpu_temp_c?: number | null;
}

interface TelemetryStats {
  total_requests?: number;
  total_tokens?: number;
}

const CLOUD_PRICING = [
  { name: 'GPT-5.6 Sol', input: 5.00, output: 30.00, primary: true },
  { name: 'Claude Fable 5', input: 10.00, output: 50.00, primary: false },
  { name: 'Gemini 3.1 Pro', input: 2.00, output: 12.00, primary: false },
];

export function SystemPanel() {
  const savings = useAppStore((s) => s.savings);
  const toggleSystemPanel = useAppStore((s) => s.toggleSystemPanel);
  const optInEnabled = useAppStore((s) => s.optInEnabled);
  const setOptInModalOpen = useAppStore((s) => s.setOptInModalOpen);
  const liveEnergy = useAppStore((s) => s.liveEnergy);
  const [energy, setEnergy] = useState<EnergyData | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryStats | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const base = getBase();
      const [energyRes, telRes] = await Promise.allSettled([
        fetch(`${base}/v1/telemetry/energy`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${base}/v1/telemetry/stats`).then((r) => (r.ok ? r.json() : null)),
      ]);
      if (energyRes.status === 'fulfilled' && energyRes.value) {
        setEnergy(energyRes.value as EnergyData);
      }
      if (telRes.status === 'fulfilled' && telRes.value) {
        setTelemetry(telRes.value as TelemetryStats);
      }
    } catch {
      // best-effort
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Re-fetch energy/telemetry when savings updates (after a chat message)
  useEffect(() => {
    if (savings) fetchData();
  }, [savings, fetchData]);

  const promptK = (savings?.total_prompt_tokens ?? 0) / 1000;
  const completionK = (savings?.total_completion_tokens ?? 0) / 1000;
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);

  const power = liveEnergy?.power_w ?? energy?.avg_power_w ?? 0;
  const energyKJ = (liveEnergy?.energy_j ?? energy?.total_energy_j ?? 0) / 1000;
  const thermal = Math.max(energy?.cpu_temp_c ?? 0, energy?.gpu_temp_c ?? 0);
  const thermalLabel = thermal === 0 ? '' : thermal < 65 ? 'Cool' : thermal < 80 ? 'Warm' : 'Hot';
  const localCost = savings?.local_cost ?? 0;
  const costs = CLOUD_PRICING.map((provider) => ({
    ...provider,
    cost: (promptK * provider.input) / 1000 + (completionK * provider.output) / 1000,
  }));
  const maxCost = Math.max(localCost, ...costs.map((c) => c.cost), 0.0001);
  const requests = savings?.total_calls ?? telemetry?.total_requests ?? 0;
  const outTokens = savings?.total_completion_tokens ?? telemetry?.total_tokens ?? 0;
  const [outValue, outUnit] = splitUnit(outTokens);

  return (
    <aside
      className="hidden md:flex flex-col h-full overflow-y-auto"
      style={{
        width: 320,
        minWidth: 320,
        background: 'var(--color-bg)',
        borderLeft: '1px solid var(--color-border)',
      }}
      aria-label="System panel"
    >
      {/* Header */}
      <div
        className="flex items-center justify-between pl-6 pr-3 shrink-0"
        style={{ height: 60, borderBottom: '1px solid var(--color-border)' }}
      >
        <span className="text-[14px]" style={{ color: 'var(--color-text)' }}>
          System
        </span>
        <div className="flex items-center gap-1">
          <Kbd>{modKey('I')}</Kbd>
          <button
            onClick={toggleSystemPanel}
            className="w-8 h-8 flex items-center justify-center rounded-full transition-colors cursor-pointer"
            style={{ color: 'var(--color-text-tertiary)' }}
            title="Close panel"
            aria-label="Close system panel"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-9 px-6 py-7">
        {/* Session */}
        <section>
          <SectionHead index="00.1" title="Session" />
          <div className="grid grid-cols-2 gap-4 pt-5">
            <div>
              <div className="hud-label mb-2">Requests</div>
              <Figure value={String(requests)} size={40} />
            </div>
            <div>
              <div className="hud-label mb-2">Output tokens</div>
              <Figure value={outValue} unit={outUnit} size={40} />
            </div>
          </div>
        </section>

        {/* Device */}
        <section>
          <SectionHead index="00.2" title="Device" right={thermalLabel} />
          <div className="flex flex-col gap-4 pt-5">
            <StatBar label="Power" value={power.toFixed(1)} unit="W" fraction={power / 80} live={isStreaming} />
            <StatBar label="Energy" value={energyKJ.toFixed(1)} unit="kJ" />
            {energy?.cpu_temp_c != null && (
              <StatBar label="CPU temp" value={String(Math.round(energy.cpu_temp_c))} unit="°C" fraction={energy.cpu_temp_c / 100} />
            )}
            {energy?.gpu_temp_c != null && (
              <StatBar label="GPU temp" value={String(Math.round(energy.gpu_temp_c))} unit="°C" fraction={energy.gpu_temp_c / 100} />
            )}
          </div>
        </section>

        {/* Cost comparison */}
        <section>
          <SectionHead index="00.3" title="Cost comparison" />
          <div className="flex flex-col gap-4 pt-5">
            <CostRow icon={HardDrive} name="Local" cost={localCost} fraction={localCost / maxCost} />
            {costs.map((c) => {
              const saved = c.cost - localCost;
              return (
                <CostRow
                  key={c.name}
                  icon={Cloud}
                  name={c.name}
                  cost={c.cost}
                  fraction={c.cost / maxCost}
                  strong={c.primary}
                  saved={saved > 0.0001 ? saved : undefined}
                />
              );
            })}
          </div>
        </section>

        {/* Leaderboard / Share */}
        <section>
          <SectionHead index="00.4" title="Leaderboard" />
          <button
            onClick={() => setOptInModalOpen(true)}
            className="sn-card w-full flex items-center gap-3 px-4 h-12 mt-5 cursor-pointer text-left"
            style={{ borderRadius: 'var(--radius-lg)' }}
          >
            <Trophy size={14} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
            <span className="text-[13px] flex-1" style={{ color: 'var(--color-text)' }}>
              {optInEnabled ? 'Sharing savings' : 'Share your savings'}
            </span>
            <span className="sn-toggle" role="switch" aria-checked={optInEnabled} aria-hidden="true" />
          </button>

          <a
            href="https://open-jarvis.github.io/OpenJarvis/leaderboard"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 mt-3 text-xs transition-colors"
            style={{ color: 'var(--color-text-tertiary)' }}
            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text)')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
          >
            <ExternalLink size={11} />
            View leaderboard
          </a>
        </section>
      </div>
    </aside>
  );
}

function StatBar({
  label,
  value,
  unit,
  fraction,
  live = false,
}: {
  label: string;
  value: string;
  unit?: string;
  fraction?: number;
  live?: boolean;
}) {
  const pct = fraction == null ? null : Math.max(0, Math.min(1, fraction)) * 100;
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[12.5px]" style={{ color: 'var(--color-text-secondary)' }}>{label}</span>
        <span className="text-[13px] tabular-nums" style={{ color: 'var(--color-text)' }}>
          {value}
          {unit && <span className="ml-1 text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>{unit}</span>}
        </span>
      </div>
      <div className="sn-bar">
        {pct != null && <i style={{ width: `${pct}%`, background: live ? 'var(--color-accent)' : undefined }} />}
      </div>
    </div>
  );
}

function CostRow({
  icon: Icon,
  name,
  cost,
  fraction,
  strong = false,
  saved,
}: {
  icon: typeof Cloud;
  name: string;
  cost: number;
  fraction: number;
  strong?: boolean;
  saved?: number;
}) {
  const text = cost.toFixed(4);
  return (
    <div>
      <div className="flex items-center gap-2.5 mb-2">
        <Icon size={13} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
        <span className="text-[12.5px] flex-1 truncate" style={{ color: strong ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>
          {name}
        </span>
        <span className="text-[13px] tabular-nums" style={{ color: 'var(--color-text)' }}>
          ${text.slice(0, -2)}
          <span style={{ color: 'var(--color-text-tertiary)' }}>{text.slice(-2)}</span>
        </span>
      </div>
      <div className="sn-bar">
        <i style={{ width: `${Math.max(0, Math.min(1, fraction)) * 100}%`, background: strong ? 'var(--color-text)' : 'var(--color-text-tertiary)' }} />
      </div>
      {saved != null && (
        <div className="text-[11.5px] mt-1.5 tabular-nums" style={{ color: 'var(--color-success)' }}>
          ${saved.toFixed(4)} <span style={{ color: 'var(--color-text-tertiary)' }}>saved</span>
        </div>
      )}
    </div>
  );
}

function splitUnit(n: number): [string, string] {
  if (n >= 1_000_000) return [(n / 1_000_000).toFixed(1), 'M'];
  if (n >= 1_000) return [(n / 1_000).toFixed(1), 'K'];
  return [String(n), ''];
}
