import { Fragment } from 'react';
import { useLocation } from 'react-router';
import { Kbd } from './ui/seneca';
import { useAppStore } from '../lib/store';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
export const MOD_KEY = isMac ? '⌘' : 'Ctrl';
/** A shortcut label: '⌘K' on a Mac, 'Ctrl K' elsewhere. */
export const modKey = (key: string) => (isMac ? `⌘${key}` : `Ctrl ${key}`);

type Hint = { keys: string[]; label: string };

function hintsFor(path: string): Hint[] {
  const models: Hint = { keys: [`${modKey('K')}`], label: 'Models' };
  switch (path) {
    case '/':
      return [
        { keys: ['Enter'], label: 'Send' },
        { keys: ['Shift', 'Enter'], label: 'New line' },
        models,
        { keys: [`${modKey('I')}`], label: 'System panel' },
        { keys: ['Esc'], label: 'Stop' },
      ];
    case '/dashboard':
      return [{ keys: ['[', ']'], label: 'Previous or next trace' }, models];
    case '/data-sources':
    case '/settings':
      return [{ keys: ['[', ']'], label: path === '/settings' ? 'Previous or next section' : 'Previous or next tab' }, models];
    default:
      return [models];
  }
}

/**
 * The bottom key hint bar: one of the three HUD traces kept by the face wash.
 * It teaches the shortcuts for the current screen and stays quiet.
 */
export function HintBar() {
  const location = useLocation();
  const serverInfo = useAppStore((s) => s.serverInfo);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const hints = hintsFor(location.pathname);
  const model = selectedModel || serverInfo?.model;

  return (
    <footer
      className="hidden md:flex items-center gap-6 px-6 shrink-0 text-xs"
      style={{
        height: 36,
        background: 'var(--color-chrome)',
        borderTop: '1px solid var(--color-border)',
        color: 'var(--color-text-tertiary)',
      }}
    >
      {hints.map((h) => (
        <span key={h.label} className="flex items-center gap-1.5">
          {h.keys.map((k, i) => (
            <Fragment key={i}>
              <Kbd>{k}</Kbd>
            </Fragment>
          ))}
          <span className="ml-1">{h.label}</span>
        </span>
      ))}
      <span className="ml-auto truncate">
        local inference{model ? ` · ${model}` : ''}
      </span>
    </footer>
  );
}
