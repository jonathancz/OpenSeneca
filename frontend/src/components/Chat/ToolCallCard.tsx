import { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, CheckCircle2, XCircle } from 'lucide-react';
import type { ToolCallInfo } from '../../types';
import { serializeToolCallArguments } from '../../lib/tool-call';

interface Props {
  toolCall: ToolCallInfo;
}

const statusConfig = {
  running: { icon: Loader2, color: 'var(--color-accent)' },
  success: { icon: CheckCircle2, color: 'var(--color-success)' },
  error: { icon: XCircle, color: 'var(--color-error)' },
};

function previewArgs(raw: string): string {
  if (!raw) return '';
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const entries = Object.entries(parsed);
      if (entries.length === 0) return '';
      const [k, v] = entries[0];
      const valStr =
        typeof v === 'string' ? v : JSON.stringify(v);
      const trimmed = valStr.length > 40 ? `${valStr.slice(0, 40)}…` : valStr;
      return entries.length === 1 ? `${k}: ${trimmed}` : `${k}: ${trimmed}, …`;
    }
  } catch {
    /* fall through */
  }
  return raw.length > 60 ? `${raw.slice(0, 60)}…` : raw;
}

function formatLatency(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

const labelStyle = {
  color: 'var(--color-text-tertiary)',
  fontSize: 11.5,
  marginBottom: 4,
} as const;

const preStyle = {
  background: 'var(--color-code-bg)',
  color: 'var(--color-text-secondary)',
  fontFamily: 'var(--font-code)',
  fontSize: 11.5,
  lineHeight: 1.5,
  whiteSpace: 'pre-wrap',
  border: '1px solid var(--color-border)',
} as const;

/** One numbered row of the "Tool calls" card (see ToolCallList). */
export function ToolCallCard({ toolCall, index = 1 }: Props & { index?: number }) {
  const [expanded, setExpanded] = useState(false);
  const config = statusConfig[toolCall.status];
  const StatusIcon = config.icon;
  // Persisted conversations may contain the pre-fix object payload despite
  // the TypeScript contract, so normalize again at the final render boundary.
  const argumentsText = serializeToolCallArguments(toolCall.arguments);
  const preview = previewArgs(argumentsText);

  return (
    <div className="text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-3 w-full py-2.5 cursor-pointer text-left"
        style={{ background: 'transparent' }}
        aria-expanded={expanded}
      >
        <span className="tabular-nums w-5 shrink-0" style={{ color: 'var(--color-text-tertiary)' }}>
          {String(index).padStart(2, '0')}
        </span>
        <StatusIcon
          size={12}
          strokeWidth={1.75}
          style={{ color: config.color, flexShrink: 0 }}
          className={toolCall.status === 'running' ? 'animate-spin' : ''}
        />
        <span
          style={{ color: 'var(--color-text)', fontFamily: 'var(--font-code)', fontSize: 12.5, flexShrink: 0 }}
        >
          {toolCall.tool}
        </span>
        {preview && !expanded && (
          <span
            className="truncate"
            style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-code)', fontSize: 11.5 }}
          >
            {preview}
          </span>
        )}
        <div className="flex-1" />
        {toolCall.latency != null && (
          <span className="tabular-nums shrink-0" style={{ color: 'var(--color-text)' }}>
            {formatLatency(toolCall.latency)}
          </span>
        )}
        {expanded ? (
          <ChevronDown size={12} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
        ) : (
          <ChevronRight size={12} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
        )}
      </button>
      {expanded && (
        <div className="pl-8 pb-3 flex flex-col gap-2.5">
          {argumentsText && (
            <div>
              <div style={labelStyle}>Arguments</div>
              <pre className="px-3 py-2 rounded-lg overflow-auto" style={{ ...preStyle, maxHeight: 120, wordBreak: 'break-all' }}>
                {formatJson(argumentsText)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div style={labelStyle}>Result</div>
              <pre className="px-3 py-2 rounded-lg overflow-auto" style={{ ...preStyle, maxHeight: 180, wordBreak: 'break-word' }}>
                {toolCall.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** The soft "Tool calls" card: header with count and total time, numbered rows. */
export function ToolCallList({ toolCalls }: { toolCalls: ToolCallInfo[] }) {
  const total = toolCalls.reduce((sum, tc) => sum + (tc.latency ?? 0), 0);
  return (
    <div className="sn-card px-4 pt-3 pb-1" style={{ borderRadius: 'var(--radius-lg)' }}>
      <div className="flex items-center justify-between text-xs pb-1" style={{ color: 'var(--color-text-tertiary)' }}>
        <span>Tool calls</span>
        <span className="tabular-nums">
          {toolCalls.length} {toolCalls.length === 1 ? 'call' : 'calls'}
          {total > 0 ? ` · ${formatLatency(total)}` : ''}
        </span>
      </div>
      {toolCalls.map((tc, i) => (
        <div key={tc.id} style={i > 0 ? { borderTop: '1px dashed var(--color-rule)' } : undefined}>
          <ToolCallCard toolCall={tc} index={i + 1} />
        </div>
      ))}
    </div>
  );
}

function formatJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
