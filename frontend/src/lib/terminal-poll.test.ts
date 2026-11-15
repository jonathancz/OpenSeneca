import { describe, expect, it } from 'vitest';
import { startTerminalPoll } from './terminal-poll';

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('startTerminalPoll', () => {
  it('asks again two seconds after each answer, and stops when closed', async () => {
    const timers: { fn: () => void; ms: number }[] = [];
    let calls = 0;
    const seen: number[] = [];
    const stop = startTerminalPoll({
      fetch: async () => ++calls,
      onData: (d) => seen.push(d),
      setTimer: (fn, ms) => {
        timers.push({ fn, ms });
        return timers.length;
      },
      clearTimer: () => {},
    });
    await tick();
    expect(seen).toEqual([1]);
    expect(timers.map((t) => t.ms)).toEqual([2000]);
    timers[0].fn();
    await tick();
    expect(seen).toEqual([1, 2]);
    stop();
    timers[1].fn();
    await tick();
    expect(calls).toBe(2);
  });

  it('keeps polling after an error', async () => {
    const timers: (() => void)[] = [];
    const errors: unknown[] = [];
    let n = 0;
    const stop = startTerminalPoll({
      fetch: async () => {
        n++;
        if (n === 1) throw new Error('down');
        return n;
      },
      onData: () => {},
      onError: (e) => errors.push(e),
      setTimer: (fn) => timers.push(fn),
      clearTimer: () => {},
    });
    await tick();
    expect(errors).toHaveLength(1);
    timers[0]();
    await tick();
    expect(n).toBe(2);
    stop();
  });
});
