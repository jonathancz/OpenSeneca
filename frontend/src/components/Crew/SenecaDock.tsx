import { MessageCircle } from 'lucide-react';
import { ChatArea } from '../Chat/ChatArea';
import { type NudgeStatus, nudgeLine, nudgeRules } from '../../lib/crew';

/**
 * Seneca docked beside the crew: the ordinary chat, plus a fold with the
 * Discord reach rules. Seneca answers and plans; it cannot steer, approve or
 * merge anything in the crew.
 */
export function SenecaDock({ nudges }: { nudges: NudgeStatus | null }) {
  return (
    <aside className="cw-dock" aria-label="Seneca">
      <div className="cw-dock-in">
        <details className="cw-dock-head">
          <summary>
            <MessageCircle size={14} strokeWidth={1.6} />
            Seneca
            <span className="cw-muted" style={{ marginLeft: 'auto' }}>
              {nudgeLine(nudges) ?? 'Discord reach'}
            </span>
          </summary>
          <ul className="cw-dock-rules">
            {nudgeRules(nudges).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </details>
        <div className="cw-dock-chat">
          <ChatArea docked />
        </div>
      </div>
    </aside>
  );
}
