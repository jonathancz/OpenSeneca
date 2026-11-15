import { create } from 'zustand';
import { apiFetch, getBase } from './api';
import { buildWsProtocols } from './useAgentEvents';
import type { CrewChanges, CrewTerminal, CrewTranscript, FleetResponse, NudgeStatus } from './crew';
import type { LiveSocket } from './crew-live';

/*
 * Read only client for the crew cockpit. Every request is a GET and the live
 * socket only listens. Nothing is ever sent toward the crew.
 */

async function getJson<T>(path: string): Promise<T> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const fetchFleet = () => getJson<FleetResponse>('/v1/cockpit/fleet');

export const fetchTranscript = (id: string) =>
  getJson<CrewTranscript>(`/v1/cockpit/agents/${encodeURIComponent(id)}/transcript`);

export const fetchChanges = (id: string) =>
  getJson<CrewChanges>(`/v1/cockpit/agents/${encodeURIComponent(id)}/changes`);

export const fetchNudges = () => getJson<NudgeStatus>('/v1/cockpit/nudges');

/** The latest view only capture of an agent's terminal; asking keeps it coming. */
export const fetchTerminal = (id: string) =>
  getJson<CrewTerminal>(`/v1/cockpit/agents/${encodeURIComponent(id)}/terminal`);

export function crewLiveUrl(): string {
  const base = getBase();
  const url = new URL('/v1/cockpit/live', base || window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

/** The browser side of `startCrewLive`'s `connect`. */
export function openCrewSocket(
  onMessage: (msg: unknown) => void,
  onClose: () => void,
): LiveSocket | null {
  const ws = new WebSocket(crewLiveUrl(), buildWsProtocols());
  let closed = false;
  const done = () => {
    if (closed) return;
    closed = true;
    onClose();
  };
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(String(ev.data)));
    } catch {
      /* ignore malformed frames */
    }
  };
  ws.onclose = done;
  ws.onerror = () => ws.close();
  return {
    close: () => {
      closed = true;
      ws.close();
    },
  };
}

interface CrewStore {
  response: FleetResponse | null;
  error: string | null;
  loadedAt: number | null;
  setResponse: (r: FleetResponse) => void;
  setError: (e: string) => void;
}

/** One shared copy of the fleet: the Crew page and the tab badge read it. */
export const useCrewStore = create<CrewStore>((set) => ({
  response: null,
  error: null,
  loadedAt: null,
  setResponse: (response) => set({ response, error: null, loadedAt: Date.now() }),
  setError: (error) => set({ error }),
}));

export async function refreshFleet(): Promise<void> {
  try {
    useCrewStore.getState().setResponse(await fetchFleet());
  } catch (e) {
    useCrewStore.getState().setError(e instanceof Error ? e.message : String(e));
  }
}
