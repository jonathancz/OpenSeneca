/**
 * Quiet background poll for the top bar: the local energy counter and whether
 * any managed agent is running. Started once per reachable backend; callers
 * must not restart it when unrelated state (such as savings) changes.
 */
export interface StatusPollDeps {
  fetchEnergy: () => Promise<unknown>;
  fetchManagedAgents: () => Promise<Array<{ status: string }>>;
  onEnergy: (joules: number | null) => void;
  onRunningAgent: (running: boolean) => void;
  intervalMs?: number;
}

export const STATUS_POLL_INTERVAL_MS = 30_000;

/** Poll now and then every `intervalMs`. Returns the stop function. */
export function startStatusPolling({
  fetchEnergy,
  fetchManagedAgents,
  onEnergy,
  onRunningAgent,
  intervalMs = STATUS_POLL_INTERVAL_MS,
}: StatusPollDeps): () => void {
  const poll = () => {
    fetchEnergy()
      .then((d) => {
        const j = (d as { total_energy_j?: number } | null)?.total_energy_j;
        onEnergy(typeof j === 'number' ? j : null);
      })
      .catch(() => {});
    fetchManagedAgents()
      .then((agents) => onRunningAgent(agents.some((a) => a.status === 'running')))
      .catch(() => {});
  };
  poll();
  const id = setInterval(poll, intervalMs);
  return () => clearInterval(id);
}
