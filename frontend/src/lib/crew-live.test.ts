import { describe, expect, it } from 'vitest';
import { startCrewLive } from './crew-live';

/** A hand cranked clock for timers. */
function fakeTimers() {
  let now = 0;
  let next = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    setTimer: (fn: () => void, ms: number) => {
      const id = next++;
      timers.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimer: (id: unknown) => {
      timers.delete(id as number);
    },
    advance(ms: number) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = end;
    },
    pending: () => timers.size,
  };
}

function harness() {
  const clock = fakeTimers();
  let refreshes = 0;
  const sockets: { onMessage: (m: unknown) => void; onClose: () => void; closed: boolean }[] = [];
  const stop = startCrewLive({
    connect: (onMessage, onClose) => {
      const s = { onMessage, onClose, closed: false };
      sockets.push(s);
      return { close: () => (s.closed = true) };
    },
    refresh: async () => {
      refreshes++;
    },
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    pollMs: 60_000,
    debounceMs: 400,
  });
  return { clock, sockets, stop, refreshes: () => refreshes };
}

describe('startCrewLive', () => {
  it('fetches once, then only when the feed says the fleet changed', () => {
    const h = harness();
    expect(h.refreshes()).toBe(1);
    h.sockets[0].onMessage({ type: 'hello', version: 1 });
    h.clock.advance(10 * 60_000);
    expect(h.refreshes()).toBe(1); // no polling while the socket is up
    h.sockets[0].onMessage({ type: 'beat' });
    h.sockets[0].onMessage({ type: 'fleet', version: 2 });
    h.sockets[0].onMessage({ type: 'fleet', version: 3 });
    h.clock.advance(399);
    expect(h.refreshes()).toBe(1);
    h.clock.advance(1);
    expect(h.refreshes()).toBe(2); // debounced into one fetch
    h.stop();
  });

  it('polls slowly and reconnects with backoff while the socket is down', () => {
    const h = harness();
    h.sockets[0].onMessage({ type: 'hello' });
    h.sockets[0].onClose();
    h.clock.advance(1000);
    expect(h.sockets).toHaveLength(2); // reconnect after 1 s
    h.sockets[1].onClose();
    h.clock.advance(1999);
    expect(h.sockets).toHaveLength(2);
    h.clock.advance(1);
    expect(h.sockets).toHaveLength(3); // then 2 s
    h.clock.advance(60_000 - 3000);
    expect(h.refreshes()).toBe(2); // one slow poll in the first minute down
    h.sockets[h.sockets.length - 1].onMessage({ type: 'hello' });
    h.clock.advance(400);
    expect(h.refreshes()).toBe(3); // catch up after reconnecting
    h.clock.advance(5 * 60_000);
    expect(h.refreshes()).toBe(3);
    h.stop();
  });

  it('stops cleanly', () => {
    const h = harness();
    h.stop();
    expect(h.sockets[0].closed).toBe(true);
    h.sockets[0].onClose();
    h.clock.advance(120_000);
    expect(h.refreshes()).toBe(1);
    expect(h.clock.pending()).toBe(0);
  });
});
