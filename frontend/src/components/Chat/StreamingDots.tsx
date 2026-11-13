interface Props {
  phase: string;
}

/** Waiting for the first token: a quiet "Generating" chip with the live dot. */
export function StreamingDots({ phase }: Props) {
  return (
    <div className="flex items-center gap-3 py-2" role="status">
      <span
        className="sn-pill"
        style={{ height: 22, color: 'var(--color-text)', borderColor: 'var(--color-accent-line)' }}
      >
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--color-accent)' }} />
        Generating
      </span>
      {phase && (
        <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          {phase}
        </span>
      )}
    </div>
  );
}
