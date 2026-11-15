/**
 * Polls an agent's live terminal mirror while its Live view is open. Each
 * request is also what asks the host feeder to keep capturing that pane, so
 * the poll stops the moment the view closes. One request at a time.
 */
export interface TerminalPollDeps<T> {
  fetch: () => Promise<T>;
  onData: (data: T) => void;
  onError?: (e: unknown) => void;
  everyMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
}

export function startTerminalPoll<T>(deps: TerminalPollDeps<T>): () => void {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
  const every = deps.everyMs ?? 2000;
  let stopped = false;
  let timer: unknown = null;

  const run = () => {
    timer = null;
    if (stopped) return;
    deps
      .fetch()
      .then((d) => {
        if (!stopped) deps.onData(d);
      })
      .catch((e) => {
        if (!stopped) deps.onError?.(e);
      })
      .finally(() => {
        if (!stopped) timer = setTimer(run, every);
      });
  };
  run();
  return () => {
    stopped = true;
    if (timer != null) clearTimer(timer);
  };
}
