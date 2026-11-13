import { useState, useEffect, useCallback } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { SectionHead } from '../ui/seneca';

interface TraceStepData {
  model?: string;
  tokens?: number;
  tool?: string;
  input?: string;
  output?: string;
  [key: string]: unknown;
}

interface TraceStep {
  step_type: string;
  duration_ms: number;
  data: TraceStepData;
}

interface TraceSummary {
  id: string;
  query: string;
  steps: TraceStep[];
  created_at: string;
}

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function StepBadge({ type }: { type: string }) {
  return (
    <span className="sn-pill" style={{ height: 22, fontSize: 11.5 }}>
      {type.replace(/_/g, ' ')}
    </span>
  );
}

function TraceCard({ trace, isActive, onClick }: { trace: TraceSummary; isActive: boolean; onClick: () => void }) {
  const totalMs = trace.steps.reduce((sum, s) => sum + s.duration_ms, 0);

  return (
    <button
      onClick={onClick}
      className="relative w-full text-left pl-6 pr-3 py-3 rounded-lg transition-colors cursor-pointer"
      style={{
        background: isActive ? 'var(--color-bg-tertiary)' : 'transparent',
      }}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--color-bg-secondary)'; }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
      aria-current={isActive ? 'true' : undefined}
    >
      {isActive && (
        <span className="absolute left-2.5 top-[19px] w-1 h-1" style={{ background: 'var(--color-accent)' }} aria-hidden="true" />
      )}
      <div className="text-[13.5px] truncate mb-1" style={{ color: isActive ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>
        {trace.query || 'Untitled query'}
      </div>
      <div className="flex items-center gap-2 text-[11.5px] tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>{trace.steps.length} steps</span>
        <span>·</span>
        <span>{formatMs(totalMs)}</span>
        <span>·</span>
        <span>{new Date(trace.created_at).toLocaleTimeString()}</span>
      </div>
    </button>
  );
}

function StepDetail({
  step,
  index,
  offsetMs,
  totalMs,
  slowest,
}: {
  step: TraceStep;
  index: number;
  offsetMs: number;
  totalMs: number;
  slowest: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const dataEntries = Object.entries(step.data).filter(([_, v]) => v != null);
  const left = totalMs > 0 ? (offsetMs / totalMs) * 100 : 0;
  const width = totalMs > 0 ? Math.max(1.5, (step.duration_ms / totalMs) * 100) : 0;

  return (
    <div style={index > 0 ? { borderTop: '1px dashed var(--color-rule)' } : undefined}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="grid items-center gap-3 w-full py-3 text-[13px] cursor-pointer text-left"
        style={{ gridTemplateColumns: '28px 120px 1fr 72px 14px' }}
        aria-expanded={expanded}
      >
        <span className="text-xs tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
          {String(index + 1).padStart(2, '0')}
        </span>
        <span><StepBadge type={step.step_type} /></span>
        <span className="relative h-2 rounded-full" style={{ background: 'rgba(255,255,255,0.03)' }}>
          <span
            className="absolute top-0 bottom-0 rounded-full"
            style={{
              left: `${left}%`,
              width: `${width}%`,
              background: slowest ? 'var(--color-accent)' : 'var(--color-text-tertiary)',
            }}
          />
        </span>
        <span className="text-right tabular-nums" style={{ color: slowest ? 'var(--color-text)' : 'var(--color-text-secondary)' }}>
          {formatMs(step.duration_ms)}
        </span>
        {expanded ? <ChevronDown size={13} style={{ color: 'var(--color-text-tertiary)' }} /> : <ChevronRight size={13} style={{ color: 'var(--color-text-tertiary)' }} />}
      </button>
      {expanded && dataEntries.length > 0 && (
        <div className="pl-10 pb-3 text-xs">
          {dataEntries.map(([key, value]) => (
            <div key={key} className="flex gap-3 py-1">
              <span className="shrink-0" style={{ color: 'var(--color-text-tertiary)', minWidth: '80px' }}>
                {key}
              </span>
              <span className="truncate" style={{ color: 'var(--color-text-secondary)', fontFamily: 'var(--font-code)' }}>
                {typeof value === 'object' ? JSON.stringify(value) : String(value)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function TraceDebugger() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchTraces = useCallback(async () => {
    try {
      const base = import.meta.env.VITE_API_URL || '';
      const res = await fetch(`${base}/v1/traces?limit=50`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      setTraces(data.traces || []);
      setError(null);
    } catch {
      setError('Cannot load traces');
    }
  }, []);

  useEffect(() => {
    fetchTraces();
  }, [fetchTraces]);

  const selected = traces.find((t) => t.id === selectedId);

  // [ and ] step through traces (shown in the footer hint bar).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key !== '[' && e.key !== ']') return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return;
      if (traces.length === 0) return;
      e.preventDefault();
      const i = traces.findIndex((tr) => tr.id === selectedId);
      const next = i < 0 ? 0 : Math.min(traces.length - 1, Math.max(0, i + (e.key === ']' ? 1 : -1)));
      setSelectedId(traces[next].id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [traces, selectedId]);

  if (error) {
    return (
      <section className="sn-card p-6">
        <SectionHead index="00.3" title="Trace debugger" />
        <div className="h-48 flex items-center justify-center text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
          {error}
        </div>
      </section>
    );
  }

  const totalMs = selected ? selected.steps.reduce((sum, st) => sum + st.duration_ms, 0) : 0;
  const slowestMs = selected ? Math.max(...selected.steps.map((st) => st.duration_ms)) : 0;
  let offset = 0;

  return (
    <section className="sn-card p-6">
      <SectionHead
        index="00.3"
        title="Trace debugger"
        right={traces.length > 0 ? `${traces.length} ${traces.length === 1 ? 'trace' : 'traces'}` : undefined}
      />

      {traces.length === 0 ? (
        <div className="h-48 flex items-center justify-center text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
          No traces yet. Start making queries to see them here.
        </div>
      ) : (
        <div className="flex gap-6 h-80 pt-5">
          {/* Trace list */}
          <div className="w-1/3 overflow-y-auto flex flex-col gap-0.5 pr-4" style={{ borderRight: '1px dashed var(--color-rule)' }}>
            {traces.map((trace) => (
              <TraceCard
                key={trace.id}
                trace={trace}
                isActive={trace.id === selectedId}
                onClick={() => setSelectedId(trace.id)}
              />
            ))}
          </div>

          {/* Trace detail */}
          <div className="flex-1 overflow-y-auto min-w-0">
            {selected ? (
              <div className="flex flex-col">
                <div className="flex items-baseline justify-between gap-4 mb-3">
                  <div className="text-[18px] font-light tracking-[-0.01em] truncate" style={{ color: 'var(--color-text)' }}>
                    {selected.query}
                  </div>
                  <div className="text-xs tabular-nums shrink-0" style={{ color: 'var(--color-text-tertiary)' }}>
                    {formatMs(totalMs)} total
                  </div>
                </div>
                {selected.steps.map((step, i) => {
                  const at = offset;
                  offset += step.duration_ms;
                  return (
                    <StepDetail
                      key={i}
                      step={step}
                      index={i}
                      offsetMs={at}
                      totalMs={totalMs}
                      slowest={step.duration_ms === slowestMs && slowestMs > 0}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
                Select a trace, or press [ and ] to step through them
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
