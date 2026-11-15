/**
 * Keeps the Crew feed fresh without tight polling.
 *
 * One push only WebSocket (`/v1/cockpit/live`) says when the host feeder
 * wrote a new `fleet.json`; only then is the fleet fetched again (debounced).
 * While the socket is down a slow poll stands in, and the socket reconnects
 * with backoff. Everything time or network shaped is injected so this runs in
 * node tests without a DOM.
 */

export interface LiveSocket {
  close(): void;
}

export interface CrewLiveDeps {
  /** Open the live socket; call `onMessage` with each parsed message and `onClose` once. */
  connect: (onMessage: (msg: unknown) => void, onClose: () => void) => LiveSocket | null;
  /** Fetch the fleet and store it. Errors are the caller's to show. */
  refresh: () => Promise<unknown>;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
  /** Poll interval while the socket is down. */
  pollMs?: number;
  debounceMs?: number;
  maxBackoffMs?: number;
}

export function startCrewLive(deps: CrewLiveDeps): () => void {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
  const pollMs = deps.pollMs ?? 60_000;
  const debounceMs = deps.debounceMs ?? 400;
  const maxBackoffMs = deps.maxBackoffMs ?? 30_000;

  let stopped = false;
  let socket: LiveSocket | null = null;
  let connected = false;
  let retry = 0;
  let debounceTimer: unknown = null;
  let pollTimer: unknown = null;
  let reconnectTimer: unknown = null;

  const refresh = () => {
    if (stopped) return;
    deps.refresh().catch(() => {});
  };

  const debouncedRefresh = () => {
    if (debounceTimer != null) clearTimer(debounceTimer);
    debounceTimer = setTimer(() => {
      debounceTimer = null;
      refresh();
    }, debounceMs);
  };

  const schedulePoll = () => {
    if (stopped || connected || pollTimer != null) return;
    pollTimer = setTimer(() => {
      pollTimer = null;
      if (!connected) {
        refresh();
        schedulePoll();
      }
    }, pollMs);
  };

  const connect = () => {
    if (stopped) return;
    let opened: LiveSocket | null = null;
    try {
      opened = deps.connect(
        (msg) => {
          if (stopped) return;
          const type = (msg as { type?: string } | null)?.type;
          if (type === 'hello') {
            const wasDown = retry > 0;
            connected = true;
            retry = 0;
            if (pollTimer != null) {
              clearTimer(pollTimer);
              pollTimer = null;
            }
            // Catch up on anything written while the socket was down.
            if (wasDown) debouncedRefresh();
          } else if (type === 'fleet') {
            debouncedRefresh();
          }
        },
        () => {
          if (stopped) return;
          connected = false;
          socket = null;
          retry += 1;
          schedulePoll();
          const delay = Math.min(maxBackoffMs, 1000 * 2 ** Math.min(retry - 1, 5));
          reconnectTimer = setTimer(() => {
            reconnectTimer = null;
            connect();
          }, delay);
        },
      );
    } catch {
      opened = null;
    }
    socket = opened;
    if (!opened) {
      retry += 1;
      schedulePoll();
    }
  };

  refresh();
  connect();

  return () => {
    stopped = true;
    for (const t of [debounceTimer, pollTimer, reconnectTimer]) if (t != null) clearTimer(t);
    socket?.close();
    socket = null;
  };
}
