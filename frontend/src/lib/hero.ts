import { create } from 'zustand';
import { apiFetch } from './api';

/*
 * Rotating hero photography. The backend fetches one Unsplash photo every six
 * hours (the access key never reaches the browser), caches it, and serves it
 * from /v1/hero/image. Without a key or while offline it serves bundled public
 * domain photos. The image is loaded through apiFetch so it carries the local
 * server's Bearer token like every other request.
 */

export interface HeroPhoto {
  source: 'unsplash' | 'fallback' | 'none';
  id?: string;
  alt?: string;
  color?: string;
  // Unsplash attribution (source === 'unsplash')
  photographer?: string;
  photographer_url?: string;
  photo_url?: string;
  unsplash_url?: string;
  // Bundled fallback credit (source === 'fallback')
  credit?: string;
  credit_url?: string;
  license?: string;
  fetched_at?: number;
  image_url?: string;
}

export interface HeroSettings {
  enabled: boolean;
  theme: string;
}

export interface HeroStatus {
  photo: HeroPhoto;
  settings: HeroSettings;
  themes: { id: string; label: string }[];
  rotation_hours: number;
  unsplash_configured: boolean;
}

interface HeroState {
  status: HeroStatus | null;
  imageUrl: string | null;
  imageId: string | null;
  error: string | null;
  load: () => Promise<void>;
  updateSettings: (patch: Partial<HeroSettings>) => Promise<void>;
}

const REFRESH_MS = 15 * 60 * 1000;

async function loadImage(photo: HeroPhoto): Promise<string | null> {
  if (!photo.image_url) return null;
  const res = await apiFetch(photo.image_url);
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export const useHeroStore = create<HeroState>((set, get) => ({
  status: null,
  imageUrl: null,
  imageId: null,
  error: null,

  load: async () => {
    try {
      const res = await apiFetch('/v1/hero/current');
      if (!res.ok) throw new Error(`hero ${res.status}`);
      const status = (await res.json()) as HeroStatus;
      set({ status, error: null });
      const id = status.photo.id ?? null;
      if (id && id !== get().imageId) {
        const url = await loadImage(status.photo);
        if (url) {
          const previous = get().imageUrl;
          set({ imageUrl: url, imageId: id });
          if (previous) URL.revokeObjectURL(previous);
        }
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  updateSettings: async (patch) => {
    const res = await apiFetch('/v1/hero/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`Could not save photo settings (${res.status})`);
    set({ status: (await res.json()) as HeroStatus });
    // A new theme is fetched in the background; pick it up shortly after.
    if (patch.theme) setTimeout(() => void get().load(), 4000);
  },
}));

let timer: ReturnType<typeof setInterval> | null = null;
let users = 0;

/** Subscribe a component to the current hero photo (shared, refreshed every 15 min). */
export function startHeroRefresh(): () => void {
  users += 1;
  if (users === 1) {
    void useHeroStore.getState().load();
    timer = setInterval(() => void useHeroStore.getState().load(), REFRESH_MS);
  }
  return () => {
    users -= 1;
    if (users === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}
