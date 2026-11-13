import { EnergyHero, EnergyStats, useEnergyData } from '../components/Dashboard/EnergyDashboard';
import { CostComparison, CLOUD_PRICING } from '../components/Dashboard/CostComparison';
import { TraceDebugger } from '../components/Dashboard/TraceDebugger';
import { useAppStore } from '../lib/store';

function Lede() {
  const savings = useAppStore((s) => s.savings);
  if (!savings || savings.total_calls === 0) {
    return (
      <h1 className="sn-lede text-[30px] md:text-[36px] max-w-[880px]">
        <b>System overview.</b> Power, tokens and savings appear here after the first reply.
      </h1>
    );
  }
  // Compare against the most expensive listed cloud model, as the cost card does.
  const promptK = savings.total_prompt_tokens / 1000;
  const completionK = savings.total_completion_tokens / 1000;
  const top = CLOUD_PRICING.map((p) => ({
    name: p.name,
    cost: (promptK * p.input) / 1000 + (completionK * p.output) / 1000,
  })).sort((a, b) => b.cost - a.cost)[0];

  return (
    <h1 className="sn-lede text-[30px] md:text-[36px] max-w-[900px]">
      <b>{savings.total_calls.toLocaleString()} requests</b> answered on this machine for{' '}
      <b className="sn-band whitespace-nowrap">${savings.local_cost.toFixed(2)} of electricity</b>, against ${top.cost.toFixed(2)} on {top.name}
    </h1>
  );
}

export function DashboardPage() {
  const now = new Date();
  const stamp = now.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  const { energy, telemetry, chartData, error } = useEnergyData();

  return (
    <div className="flex-1 overflow-y-auto px-6 md:px-14 pt-12 pb-16">
      <div className="max-w-[1180px] mx-auto">
        <header className="flex flex-col lg:flex-row lg:items-end justify-between gap-6 mb-10">
          <Lede />
          <div className="flex flex-col lg:items-end gap-2 shrink-0">
            <span className="sn-pill self-start lg:self-end" style={{ height: 22 }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: error ? 'var(--color-error)' : 'var(--color-success)' }} />
              {error ? 'Offline' : 'Live · 5 s refresh'}
            </span>
            <div className="text-xs tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>{stamp}</div>
          </div>
        </header>

        <EnergyStats energy={energy} telemetry={telemetry} />

        <div className="grid grid-cols-1 lg:grid-cols-[1.45fr_1fr] gap-6 mt-12 mb-6">
          <EnergyHero energy={energy} chartData={chartData} error={error} />
          <CostComparison />
        </div>

        <TraceDebugger />
      </div>
    </div>
  );
}
