import type { CSSProperties, ReactNode } from 'react';

/*
 * Small building blocks for the Seneca face wash (mockup v3): thin figures
 * with dimmed decimals, 00.1 section heads with dashed rules, keycaps and
 * pill tabs. Styling lives in index.css (.sn-*); these only set structure.
 */

/** Split a formatted number so the decimals can be dimmed. */
export function splitDecimal(text: string): [string, string] {
  const m = /^(.*?)([.,]\d+)?$/.exec(text);
  if (!m) return [text, ''];
  return [m[1], m[2] ?? ''];
}

export function Figure({
  value,
  unit,
  size = 40,
  className,
  style,
}: {
  value: string;
  unit?: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  const [whole, dec] = splitDecimal(value);
  return (
    <span className={`sn-figure ${className ?? ''}`} style={{ fontSize: size, ...style }}>
      {whole}
      {dec && <span className="sn-dec">{dec}</span>}
      {unit && <span className="sn-unit">{unit}</span>}
    </span>
  );
}

export function SectionHead({
  index,
  title,
  right,
  className,
  style,
}: {
  index?: string;
  title: ReactNode;
  right?: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`sn-section-head ${className ?? ''}`} style={style}>
      {index && <span className="sn-index">{index}</span>}
      <span className="flex-1 min-w-0 truncate">{title}</span>
      {right && (
        <span className="text-xs shrink-0" style={{ color: 'var(--color-text-tertiary)' }}>
          {right}
        </span>
      )}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="sn-kbd">{children}</kbd>;
}

export function PillTabs<T extends string>({
  tabs,
  value,
  onChange,
  showKeys = false,
  ariaLabel,
}: {
  tabs: { id: T; label: ReactNode; icon?: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  showKeys?: boolean;
  ariaLabel?: string;
}) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      {showKeys && <Kbd>[</Kbd>}
      <div
        role="tablist"
        aria-label={ariaLabel}
        className="flex items-center gap-1 p-1 rounded-full overflow-x-auto"
        style={{ border: '1px solid var(--color-border)', background: 'rgba(255,255,255,0.015)' }}
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={value === t.id}
            data-active={value === t.id}
            onClick={() => onChange(t.id)}
            className="sn-tab"
            style={{ height: 30, padding: '0 14px' }}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>
      {showKeys && <Kbd>]</Kbd>}
    </div>
  );
}
