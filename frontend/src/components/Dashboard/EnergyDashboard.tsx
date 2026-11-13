import { useState, useEffect, useCallback } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { fetchEnergy, fetchTelemetry } from '../../lib/api';
import { useAppStore } from '../../lib/store';
import { Figure } from '../ui/seneca';
import { HeroBackdrop, PhotoCredit } from '../HeroPhoto';

interface EnergySample {
  timestamp: string;
  power_w: number;
  energy_j: number;
}

export interface EnergyData {
  total_energy_j?: number;
  energy_per_token_j?: number;
  avg_power_w?: number;
  cpu_temp_c?: number | null;
  gpu_temp_c?: number | null;
  samples?: EnergySample[];
}

export interface TelemetryStats {
  total_requests?: number;
  total_tokens?: number;
}

interface ChartPoint {
  time: string;
  power: number;
}

/** Energy and telemetry, polled every 5 s (one poller for the whole dashboard). */
export function useEnergyData() {
  const [energy, setEnergy] = useState<EnergyData | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryStats | null>(null);
  const [chartData, setChartData] = useState<ChartPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [energyRes, telRes] = await Promise.allSettled([
        fetchEnergy().catch(() => null),
        fetchTelemetry().catch(() => null),
      ]);

      if (energyRes.status === 'fulfilled' && energyRes.value) {
        const data = energyRes.value as EnergyData;
        setEnergy(data);
        if (data.samples) {
          setChartData(
            data.samples.map((s) => ({
              time: new Date(s.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              power: Math.round(s.power_w * 10) / 10,
            })),
          );
        }
        setError(null);
      }
      if (telRes.status === 'fulfilled' && telRes.value) {
        setTelemetry(telRes.value as TelemetryStats);
      }
    } catch {
      setError('Cannot connect to server');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return { energy, telemetry, chartData, error };
}

export function thermalStatus(energy: EnergyData | null) {
  const w = energy?.avg_power_w ?? 0;
  return w < 50 ? 'Cool' : w < 150 ? 'Warm' : 'Hot';
}

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function splitUnit(n: number): [string, string] {
  if (n >= 1_000_000) return [(n / 1_000_000).toFixed(1), 'M'];
  if (n >= 1_000) return [(n / 1_000).toFixed(1), 'K'];
  return [String(n), ''];
}

/** The four open figures under the headline. */
export function EnergyStats({ energy, telemetry }: { energy: EnergyData | null; telemetry: TelemetryStats | null }) {
  const savings = useAppStore((s) => s.savings);
  const requests = savings?.total_calls ?? telemetry?.total_requests ?? 0;
  const tokens = savings?.total_tokens ?? telemetry?.total_tokens ?? 0;
  const [tokValue, tokUnit] = splitUnit(tokens);
  const temps = [
    energy?.cpu_temp_c != null ? `CPU ${Math.round(energy.cpu_temp_c)} °C` : null,
    energy?.gpu_temp_c != null ? `GPU ${Math.round(energy.gpu_temp_c)} °C` : null,
  ].filter(Boolean).join(' · ');

  const stats = [
    { label: 'Avg power', value: (energy?.avg_power_w ?? 0).toFixed(1), unit: 'W', sub: 'across recent samples' },
    { label: 'Total requests', value: String(requests), sub: 'since the server started' },
    {
      label: 'Tokens processed',
      value: tokValue,
      unit: tokUnit,
      sub: savings
        ? `${formatNumber(savings.total_prompt_tokens)} prompt · ${formatNumber(savings.total_completion_tokens)} output`
        : 'prompt and output',
    },
    { label: 'Thermal', value: thermalStatus(energy), sub: temps || 'from average power draw' },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-8 gap-y-8 pt-7" style={{ borderTop: '1px dashed var(--color-rule)' }}>
      {stats.map((s) => (
        <div key={s.label} className="min-w-0">
          <div className="hud-label mb-3">{s.label}</div>
          <Figure value={s.value} unit={s.unit} size={52} />
          <div className="text-xs mt-3 truncate" style={{ color: 'var(--color-text-tertiary)' }}>{s.sub}</div>
        </div>
      ))}
    </div>
  );
}

/**
 * The dashboard hero: the rotating photo, darkened, with frosted glass
 * readouts for energy and the power chart. Carries the one HUD notch.
 */
export function EnergyHero({
  energy,
  chartData,
  error,
}: {
  energy: EnergyData | null;
  chartData: ChartPoint[];
  error: string | null;
}) {
  const totalKJ = ((energy?.total_energy_j ?? 0) / 1000).toFixed(1);
  const avgW = (energy?.avg_power_w ?? 0).toFixed(1);

  return (
    <section
      className="sn-notch relative overflow-hidden min-h-[460px] flex flex-col justify-between p-6 md:p-7"
      style={{ border: '1px solid var(--color-border)', background: 'var(--color-bg-secondary)' }}
      aria-label="Energy monitoring"
    >
      <HeroBackdrop position="center 40%" />

      <div className="relative sn-glass p-6 max-w-[320px]">
        <div className="flex items-center gap-2.5 text-[13.5px]" style={{ color: 'var(--color-text)' }}>
          <span className="w-1 h-1" style={{ background: 'var(--color-accent)' }} aria-hidden="true" />
          Energy monitoring
        </div>
        {error || !energy ? (
          <div className="text-[13px] mt-6" style={{ color: 'var(--color-text-secondary)' }}>
            {error || 'Waiting for telemetry…'}
          </div>
        ) : (
          <>
            <div className="mt-5">
              <Figure value={totalKJ} unit="kJ" size={84} />
            </div>
            <div className="text-[13px] mt-4" style={{ color: 'var(--color-text-secondary)' }}>
              <span style={{ color: 'var(--color-text)' }}>{(energy.energy_per_token_j ?? 0).toFixed(3)} J</span> per token, total energy since start
            </div>
          </>
        )}
      </div>

      <div className="relative flex flex-col gap-3 mt-8">
        {chartData.length > 1 && (
          <div className="sn-glass px-5 pt-4 pb-3 max-w-[520px]">
            <div className="flex items-baseline justify-between mb-2">
              <span className="flex items-baseline gap-3 text-[13.5px]" style={{ color: 'var(--color-text)' }}>
                Power draw
                <Figure value={avgW} unit="W" size={30} />
              </span>
              <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>recent samples</span>
            </div>
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 6, right: 4, bottom: 0, left: -18 }}>
                  <CartesianGrid strokeDasharray="4 5" stroke="rgba(255,255,255,0.10)" vertical={false} />
                  <XAxis
                    dataKey="time"
                    tick={{ fontSize: 11, fill: 'var(--color-text-tertiary)' }}
                    axisLine={false}
                    tickLine={false}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: 'var(--color-text-tertiary)' }}
                    axisLine={false}
                    tickLine={false}
                    unit=" W"
                    width={48}
                  />
                  <Tooltip
                    cursor={{ stroke: 'var(--color-accent)', strokeDasharray: '3 3' }}
                    contentStyle={{
                      background: 'var(--color-bg-tertiary)',
                      border: '1px solid var(--color-border-strong)',
                      borderRadius: 10,
                      fontSize: 12,
                      color: 'var(--color-text)',
                    }}
                    labelStyle={{ color: 'var(--color-text-tertiary)' }}
                  />
                  <Line
                    type="monotone"
                    dataKey="power"
                    stroke="var(--color-text)"
                    strokeWidth={1.25}
                    dot={false}
                    activeDot={{ r: 3, fill: 'var(--color-accent)', stroke: 'none' }}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
        <PhotoCredit className="self-end" />
      </div>
    </section>
  );
}
