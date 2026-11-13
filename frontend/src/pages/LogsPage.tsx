import { useRef, useEffect } from 'react';
import { Copy, Trash2 } from 'lucide-react';
import { useAppStore } from '../lib/store';

const LEVEL_COLORS: Record<string, string> = {
  info: 'var(--color-text-secondary)',
  warn: 'var(--color-warning)',
  error: 'var(--color-error)',
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function LogsPage() {
  const logEntries = useAppStore((s) => s.logEntries);
  const clearLogs = useAppStore((s) => s.clearLogs);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logEntries.length]);

  const handleCopy = async () => {
    const text = logEntries
      .map((e) => `${formatTime(e.timestamp)} [${e.level}] [${e.category}] ${e.message}`)
      .join('\n');
    await navigator.clipboard.writeText(text);
  };

  const buttonClass = 'flex items-center gap-1.5 h-9 px-3.5 rounded-[10px] text-[12.5px] transition-colors cursor-pointer';
  const buttonStyle = { background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-input-border)' };

  return (
    <div className="flex-1 flex flex-col overflow-hidden px-6 md:px-14 pt-12 pb-10">
      <div className="max-w-[1180px] mx-auto w-full flex flex-col flex-1 overflow-hidden">
        <header className="mb-8 shrink-0 flex flex-col lg:flex-row lg:items-end justify-between gap-6">
          <h1 className="sn-lede text-[30px] md:text-[36px] max-w-[820px]">
            <b>Logs.</b> Chat events, model switches, tool calls and system messages from this session.
          </h1>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs mr-2 tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>
              {logEntries.length} entries
            </span>
            <button onClick={handleCopy} className={buttonClass} style={buttonStyle}>
              <Copy size={12} strokeWidth={1.75} /> Copy all
            </button>
            <button onClick={clearLogs} className={buttonClass} style={buttonStyle}>
              <Trash2 size={12} strokeWidth={1.75} /> Clear
            </button>
          </div>
        </header>

        {/* Log entries */}
        <div className="sn-card flex-1 overflow-y-auto px-6 py-4 text-[12.5px] leading-relaxed">
          {logEntries.length === 0 ? (
            <div className="text-center py-12 text-[13px]" style={{ color: 'var(--color-text-tertiary)' }}>
              No log entries yet. Logs appear as you chat, switch models, and interact with the app.
            </div>
          ) : (
            logEntries.map((entry, i) => (
              <div
                key={i}
                className="grid gap-4 py-2"
                style={{
                  gridTemplateColumns: '72px 64px 1fr',
                  borderTop: i > 0 ? '1px dashed var(--color-rule)' : undefined,
                }}
              >
                <span className="tabular-nums" style={{ color: 'var(--color-text-tertiary)' }}>{formatTime(entry.timestamp)}</span>
                <span style={{ color: 'var(--color-text-tertiary)' }}>{entry.category}</span>
                <span style={{ color: LEVEL_COLORS[entry.level] || 'var(--color-text)', fontFamily: 'var(--font-code)', fontSize: 12 }}>
                  {entry.message}
                </span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
