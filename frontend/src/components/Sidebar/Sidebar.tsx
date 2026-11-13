import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Plus, Search, PanelLeftClose, PanelLeft, Loader2 } from 'lucide-react';
import { ConversationList } from './ConversationList';
import { useAppStore } from '../../lib/store';
import { modKey } from '../HintBar';
import { Kbd } from '../ui/seneca';

/** Chat sidebar: model card, new chat, search and the conversation list. */
export function Sidebar() {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');

  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const createConversation = useAppStore((s) => s.createConversation);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const setCommandPaletteOpen = useAppStore((s) => s.setCommandPaletteOpen);
  const modelLoading = useAppStore((s) => s.modelLoading);
  const deepResearch = useAppStore((s) => s.deepResearch);

  const messages = useAppStore((s) => s.messages);
  const handleNewChat = () => {
    // Don't create a new chat if the current one is empty
    if (messages.length === 0) {
      navigate('/');
      return;
    }
    createConversation(selectedModel);
    navigate('/');
  };

  const modelName = deepResearch
    ? 'Deep research'
    : selectedModel || serverInfo?.model || 'Select model';
  const modelMeta = [serverInfo?.engine, serverInfo?.agent ? `${serverInfo.agent} agent` : null]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {/* Reopen button when the sidebar is collapsed */}
      {!sidebarOpen && (
        <button
          onClick={toggleSidebar}
          className="absolute top-3 left-3 z-30 w-9 h-9 flex items-center justify-center rounded-full transition-colors cursor-pointer"
          style={{ color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)', background: 'var(--color-bg)' }}
          title="Show chats"
          aria-label="Show chats"
        >
          <PanelLeft size={16} strokeWidth={1.5} />
        </button>
      )}

      <aside
        className={`
          flex flex-col h-full shrink-0 transition-all duration-200 ease-in-out overflow-hidden
          fixed md:relative z-30
          ${sidebarOpen ? 'w-[280px]' : 'w-0'}
        `}
        style={{
          background: 'var(--color-sidebar)',
          borderRight: sidebarOpen ? '1px solid var(--color-border)' : 'none',
        }}
      >
        <div className="flex flex-col h-full w-[280px] px-4 pt-5 pb-3 gap-3">
          {/* Model card */}
          <button
            onClick={() => setCommandPaletteOpen(true)}
            className="sn-card text-left px-4 pt-3.5 pb-4 cursor-pointer"
            title={`Choose a model (${modKey('K')})`}
          >
            <div className="flex items-center justify-between">
              <span className="hud-label">Model</span>
              {modelLoading ? (
                <Loader2 size={14} className="animate-spin" style={{ color: 'var(--color-accent)' }} />
              ) : (
                <Kbd>{modKey('K')}</Kbd>
              )}
            </div>
            <div
              className="truncate mt-2.5 text-[20px] font-light tracking-[-0.02em]"
              style={{ color: deepResearch ? 'var(--color-accent)' : 'var(--color-text)' }}
            >
              {modelName}
            </div>
            <div className="truncate mt-1 text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
              {modelLoading ? 'Loading model…' : modelMeta || 'local inference'}
            </div>
          </button>

          <div className="flex items-center gap-2">
            <button
              onClick={handleNewChat}
              className="flex-1 flex items-center justify-center gap-2 h-10 rounded-lg text-[13px] cursor-pointer transition-colors"
              style={{ border: '1px solid var(--color-border-strong)', color: 'var(--color-text)', background: 'transparent' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-tertiary)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <Plus size={15} strokeWidth={1.5} />
              New chat
            </button>
            <button
              onClick={toggleSidebar}
              className="w-10 h-10 flex items-center justify-center rounded-lg transition-colors cursor-pointer"
              style={{ color: 'var(--color-text-tertiary)', border: '1px solid var(--color-border)' }}
              title="Hide chats"
              aria-label="Hide chats"
            >
              <PanelLeftClose size={15} strokeWidth={1.5} />
            </button>
          </div>

          {/* Search */}
          <label
            className="flex items-center gap-2.5 px-3.5 h-10 rounded-lg text-[13px]"
            style={{ border: '1px solid var(--color-border)', background: 'transparent' }}
          >
            <Search size={14} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
            <input
              type="text"
              placeholder="Search chats"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 bg-transparent outline-none text-[13px] placeholder:text-[var(--color-text-tertiary)]"
              style={{ color: 'var(--color-text)' }}
            />
          </label>

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto -mx-2 pt-2">
            <ConversationList searchQuery={searchQuery} />
          </div>
        </div>
      </aside>
    </>
  );
}
