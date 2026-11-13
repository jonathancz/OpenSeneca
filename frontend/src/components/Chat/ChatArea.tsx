import { useRef, useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router';
import { MessageBubble } from './MessageBubble';
import { InputArea } from './InputArea';
import { StreamingDots } from './StreamingDots';
import { useAppStore } from '../../lib/store';
import { shouldAutoplayFinishedReply, useTtsStore } from '../../lib/tts';
import { stripThinkTags } from '../../lib/message-text';
import { PanelRightOpen, PanelRightClose, Database, MessageSquare, X, ArrowUpRight } from 'lucide-react';
import { listConnectors } from '../../lib/connectors-api';
import { HeroBackdrop, PhotoCredit } from '../HeroPhoto';
import { modKey } from '../HintBar';

export function ChatArea() {
  const activeId = useAppStore((s) => s.activeId);
  const messages = useAppStore((s) => s.messages);
  const streamState = useAppStore((s) => s.streamState);
  const systemPanelOpen = useAppStore((s) => s.systemPanelOpen);
  const toggleSystemPanel = useAppStore((s) => s.toggleSystemPanel);
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const navigate = useNavigate();
  const listRef = useRef<HTMLDivElement>(null);
  const shouldAutoScroll = useRef(true);
  const wasStreaming = useRef(false);
  const lastScrollTop = useRef(0);
  const isCurrentChatStreaming = streamState.isStreaming && streamState.conversationId === activeId;

  // Autoplay: speak a reply once it is finished, never while it streams -- a
  // partial sentence would be synthesized and then cut off by the next chunk.
  // autoSpokenId fences the message so re-renders cannot repeat it.
  const voiceOutputEnabled = useAppStore((s) => s.settings.voiceOutputEnabled);
  const voiceAutoplay = useAppStore((s) => s.settings.voiceAutoplay);
  // Probe the backend as soon as voice output is switched on. Without this the
  // first reply could never autoplay: `available` is only set by ensureHealth,
  // which until now ran solely from the per-message read-aloud button -- and
  // that button does not exist until a reply is already on screen.
  useEffect(() => {
    if (!voiceOutputEnabled) return;
    useTtsStore.getState().ensureHealth();
  }, [voiceOutputEnabled]);

  // Speak on the falling edge of a stream -- the moment a reply finishes -- and
  // never merely because a finished reply happens to be on screen. Opening the
  // app or switching conversations would otherwise read stale history aloud,
  // and browsers block that anyway: playback with no preceding user gesture is
  // rejected outright, so the failure would be silent in both senses.
  const streamingConversationRef = useRef<string | null>(null);
  useEffect(() => {
    const last = messages[messages.length - 1];
    const tts = useTtsStore.getState();
    const justFinished = shouldAutoplayFinishedReply(
      streamingConversationRef.current,
      activeId,
      streamState.isStreaming,
      last,
      tts.autoSpokenId,
    );
    streamingConversationRef.current = isCurrentChatStreaming ? activeId : null;

    if (!justFinished) return;
    if (!voiceOutputEnabled || !voiceAutoplay) return;

    if (!last || last.role !== 'assistant' || activeId === null) return;

    // Model loading may still be in flight when a fast reply completes. Wait
    // for the probe, then confirm that this is still the active finished reply.
    const completedConversationId = activeId;
    const completedMessageId = last.id;
    void tts.ensureHealth().then(() => {
      const app = useAppStore.getState();
      const currentTts = useTtsStore.getState();
      const currentLast = app.messages[app.messages.length - 1];
      if (app.activeId !== completedConversationId || app.streamState.isStreaming) return;
      if (!app.settings.voiceOutputEnabled || !app.settings.voiceAutoplay) return;
      if (currentLast?.id !== completedMessageId || currentTts.available !== true) return;
      if (currentTts.autoSpokenId === completedMessageId) return;

      const text = stripThinkTags(currentLast.content);
      if (!text) return;
      currentTts.markAutoSpoken(completedMessageId);
      void currentTts.speak(completedMessageId, text);
    });
  }, [activeId, messages, streamState.isStreaming, isCurrentChatStreaming, voiceOutputEnabled, voiceAutoplay]);
  const currentStreamContent = isCurrentChatStreaming ? streamState.content : '';

  // Check if any data sources are connected
  const [hasConnectedSources, setHasConnectedSources] = useState<boolean | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    listConnectors()
      .then((list) => setHasConnectedSources(list.some((c) => c.connected)))
      .catch(() => setHasConnectedSources(null));
  }, []);

  useEffect(() => {
    // Sending a message always pins the view to the bottom, even if the
    // user had scrolled up to read earlier messages.
    if (isCurrentChatStreaming && !wasStreaming.current) {
      shouldAutoScroll.current = true;
    }
    wasStreaming.current = isCurrentChatStreaming;
    if (shouldAutoScroll.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, currentStreamContent, isCurrentChatStreaming]);

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    const distance = scrollHeight - scrollTop - clientHeight;
    const scrolledUp = scrollTop < lastScrollTop.current;
    lastScrollTop.current = scrollTop;
    if (scrolledUp && distance >= 1) {
      // Any upward scroll away from the bottom stops autoscroll immediately,
      // so streaming content never fights the user (no jitter). Sub-1px
      // upward movement (elastic bounce settling at the bottom) is ignored.
      shouldAutoScroll.current = false;
    } else if (!scrolledUp) {
      // Re-engage when scrolled back to the bottom. < 2 rather than < 1:
      // at fractional zoom levels the at-bottom residual can reach 1px,
      // which would otherwise leave autoscroll permanently disengaged.
      shouldAutoScroll.current = distance < 2;
    }
  };

  const isEmpty = messages.length === 0 && !isCurrentChatStreaming;

  const PanelIcon = systemPanelOpen ? PanelRightClose : PanelRightOpen;
  const conversation = useAppStore((s) => s.conversations.find((c) => c.id === s.activeId));
  const turns = messages.filter((m) => m.role === 'user').length;
  const title = isEmpty ? 'New chat' : conversation?.title || 'Chat';
  const meta = [conversation?.model, turns > 0 ? `${turns} ${turns === 1 ? 'turn' : 'turns'}` : null]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="flex flex-col h-full">
      {/* Title bar */}
      <div
        className={`flex items-center gap-3 ${sidebarOpen ? 'pl-16 md:pl-8' : 'pl-16'} pr-4 shrink-0`}
        style={{ height: 60, borderBottom: '1px solid var(--color-border)' }}
      >
        <h1 className="text-[17px] font-light tracking-[-0.01em] truncate" style={{ color: 'var(--color-text)' }}>
          {title}
        </h1>
        {meta && (
          <span className="text-xs truncate shrink-0" style={{ color: 'var(--color-text-tertiary)' }}>
            {meta}
          </span>
        )}
        <button
          onClick={toggleSystemPanel}
          className="ml-auto w-9 h-9 flex items-center justify-center rounded-full transition-colors cursor-pointer"
          style={{ color: 'var(--color-text-tertiary)' }}
          title={`${systemPanelOpen ? 'Hide' : 'Show'} system panel (${modKey('I')})`}
          aria-label={`${systemPanelOpen ? 'Hide' : 'Show'} system panel`}
        >
          <PanelIcon size={16} strokeWidth={1.5} />
        </button>
      </div>

      {/* Data sources banner */}
      {hasConnectedSources === false && !bannerDismissed && !isEmpty && (
        <div
          className="mx-6 mt-4 flex items-center gap-3 pl-4 pr-2 py-2.5 rounded-xl text-[13px] shrink-0"
          style={{
            background: 'var(--card-surface)',
            border: '1px solid var(--color-border)',
          }}
        >
          <Database size={15} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
          <span style={{ color: 'var(--color-text-secondary)', flex: 1 }}>
            Connect your data sources (Gmail, iMessage, Slack and more) to get personal answers.
          </span>
          <button
            onClick={() => navigate('/data-sources')}
            className="h-8 px-3 rounded-lg text-[12.5px] cursor-pointer"
            style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)', border: '1px solid var(--color-input-border)' }}
          >
            Connect
          </button>
          <button
            onClick={() => setBannerDismissed(true)}
            className="w-8 h-8 flex items-center justify-center rounded-full cursor-pointer"
            style={{ color: 'var(--color-text-tertiary)', background: 'transparent', border: 'none' }}
            aria-label="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto"
      >
        {isEmpty ? (
          <div className="h-full px-6 py-6 flex">
            <section className="relative flex-1 min-h-[360px] overflow-hidden flex flex-col justify-end sn-card" style={{ borderRadius: 'var(--radius-2xl)' }}>
              <HeroBackdrop position="center 60%" />
              <div className="relative px-8 md:px-12 pb-10 md:pb-12 pt-24 max-w-[760px]">
                <p className="hud-label mb-4 flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
                  <span className="w-1 h-1" style={{ background: 'var(--color-accent)' }} aria-hidden="true" />
                  New chat
                </p>
                <h2 className="sn-lede text-[34px] md:text-[44px]">
                  <b>Ask anything.</b> It runs on this machine, so it stays private and answers fast.
                </h2>
                <div className="flex flex-wrap gap-2.5 mt-8">
                  <button
                    onClick={() => navigate('/data-sources')}
                    className="sn-hero-action"
                  >
                    <Database size={14} strokeWidth={1.5} />
                    {hasConnectedSources ? 'Data sources' : 'Connect data sources'}
                    <ArrowUpRight size={13} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
                  </button>
                  <button
                    onClick={() => { navigate('/data-sources'); setTimeout(() => window.dispatchEvent(new CustomEvent('switch-tab', { detail: 'messaging' })), 100); }}
                    className="sn-hero-action"
                  >
                    <MessageSquare size={14} strokeWidth={1.5} />
                    Set up messaging channels
                    <ArrowUpRight size={13} strokeWidth={1.5} style={{ color: 'var(--color-text-tertiary)' }} />
                  </button>
                </div>
              </div>
              <PhotoCredit className="absolute right-6 bottom-4" />
            </section>
          </div>
        ) : (
          <div className="max-w-[var(--chat-max-width)] mx-auto px-6 py-10">
            {messages.map((msg, i) => {
              const isLastAssistant =
                i === messages.length - 1 && msg.role === 'assistant';
              return (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  isLive={isLastAssistant && isCurrentChatStreaming}
                />
              );
            })}
            {(() => {
              if (!isCurrentChatStreaming || streamState.content !== '') return null;
              // For research messages the ResearchTimeline handles its own
              // pre-content loading state — suppress the generic dots.
              const last = messages[messages.length - 1];
              if (last?.role === 'assistant' && last.isResearch) return null;
              return (
                <div className="flex justify-start mb-4">
                  <StreamingDots phase={streamState.phase} />
                </div>
              );
            })()}
          </div>
        )}
      </div>
      <InputArea />
    </div>
  );
}
