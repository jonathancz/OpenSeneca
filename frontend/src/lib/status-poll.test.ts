import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startStatusPolling } from './status-poll';

describe('top bar status polling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function start() {
    const fetchEnergy = vi.fn().mockResolvedValue({ total_energy_j: 42 });
    const fetchManagedAgents = vi.fn().mockResolvedValue([{ status: 'running' }]);
    const onEnergy = vi.fn();
    const onRunningAgent = vi.fn();
    const stop = startStatusPolling({ fetchEnergy, fetchManagedAgents, onEnergy, onRunningAgent });
    return { fetchEnergy, fetchManagedAgents, onEnergy, onRunningAgent, stop };
  }

  it('polls once on start and then once per 30 s, never more', async () => {
    const p = start();
    expect(p.fetchEnergy).toHaveBeenCalledTimes(1);
    expect(p.fetchManagedAgents).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(29_999);
    expect(p.fetchEnergy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1 + 60_000); // 30 s, 60 s and 90 s ticks
    expect(p.fetchEnergy).toHaveBeenCalledTimes(4);
    expect(p.fetchManagedAgents).toHaveBeenCalledTimes(4);
    expect(p.onEnergy).toHaveBeenLastCalledWith(42);
    expect(p.onRunningAgent).toHaveBeenLastCalledWith(true);
    p.stop();
  });

  it('stops polling after cleanup', async () => {
    const p = start();
    p.stop();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(p.fetchEnergy).toHaveBeenCalledTimes(1);
  });
});
