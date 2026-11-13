import { Cloud } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { SectionHead } from '../ui/seneca';

export const CLOUD_PRICING = [
  { name: 'GPT-5.6 Sol', input: 5.00, output: 30.00 },
  { name: 'Claude Fable 5', input: 10.00, output: 50.00 },
  { name: 'Gemini 3.1 Pro', input: 2.00, output: 12.00 },
];

function Money({ value }: { value: number }) {
  const text = value.toFixed(4);
  return (
    <span className="tabular-nums">
      ${text.slice(0, -2)}
      <span style={{ color: 'var(--color-text-tertiary)' }}>{text.slice(-2)}</span>
    </span>
  );
}

export function CostComparison() {
  const savings = useAppStore((s) => s.savings);

  if (!savings || savings.total_tokens === 0) {
    return (
      <section className="sn-card p-6">
        <SectionHead index="00.2" title="Cost comparison" right="estimate" />
        <div className="h-48 flex items-center justify-center text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
          Waiting for the first reply…
        </div>
      </section>
    );
  }

  const promptK = savings.total_prompt_tokens / 1000;
  const completionK = savings.total_completion_tokens / 1000;
  const rows = CLOUD_PRICING.map((provider) => {
    const cost = (promptK * provider.input / 1000) + (completionK * provider.output / 1000);
    return { ...provider, cost, saved: cost - savings.local_cost };
  });
  const maxCost = Math.max(...rows.map((r) => r.cost), 0.0001);
  const top = Math.max(...rows.map((r) => r.cost));

  return (
    <section className="sn-card p-6 flex flex-col">
      <SectionHead index="00.2" title="Cost comparison" right="estimate*" />

      {/* Local */}
      <div className="pt-6 pb-6" style={{ borderBottom: '1px dashed var(--color-rule)' }}>
        <div className="hud-label mb-3">Local, your hardware</div>
        <span className="sn-figure" style={{ fontSize: 56 }}>
          ${savings.local_cost.toFixed(4).slice(0, -2)}
          <span className="sn-dec">{savings.local_cost.toFixed(4).slice(-2)}</span>
        </span>
        <div className="text-xs mt-3" style={{ color: 'var(--color-text-tertiary)' }}>
          electricity only · {savings.total_calls} requests · {savings.total_tokens.toLocaleString()} tokens
        </div>
      </div>

      {/* Cloud comparisons */}
      <div className="flex flex-col gap-5 pt-6">
        {rows.map((r) => (
          <div key={r.name}>
            <div className="flex items-center gap-2.5 mb-2 text-[13px]">
              <Cloud size={13} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
              <span className="flex-1" style={{ color: 'var(--color-text-secondary)' }}>{r.name}</span>
              <span style={{ color: 'var(--color-text)' }}><Money value={r.cost} /></span>
            </div>
            <div className="sn-bar">
              <i style={{ width: `${(r.cost / maxCost) * 100}%`, background: r.cost === top ? 'var(--color-text)' : 'var(--color-text-tertiary)' }} />
            </div>
            {r.saved > 0 && (
              <div className="text-[11.5px] mt-1.5 tabular-nums" style={{ color: 'var(--color-success)' }}>
                ${r.saved.toFixed(4)} <span style={{ color: 'var(--color-text-tertiary)' }}>saved</span>
              </div>
            )}
          </div>
        ))}
      </div>

      <p className="text-[11.5px] leading-relaxed mt-auto pt-6" style={{ color: 'var(--color-text-tertiary)' }}>
        *Savings estimates assume local models (e.g. Qwen, Nemotron, Kimi) produce roughly the same number of tokens per request, on average, as closed-source cloud models.
      </p>
    </section>
  );
}
