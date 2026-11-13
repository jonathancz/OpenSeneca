import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The hero photo comes only from the local server (/v1/hero/*): the browser
// never talks to Unsplash and never sees the access key. These tests mock
// fetch and check the store loads metadata, fetches the image once per photo
// through the authenticated apiFetch, and saves settings.

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(k: string): string | null {
    return this.store.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.store.set(k, String(v));
  }
  removeItem(k: string): void {
    this.store.delete(k);
  }
}

const fetchMock = vi.fn<typeof fetch>();

function status(id: string, extra: Record<string, unknown> = {}) {
  return {
    photo: {
      source: 'unsplash',
      id,
      photographer: 'Ana',
      photographer_url: 'https://unsplash.com/@ana?utm_source=seneca&utm_medium=referral',
      unsplash_url: 'https://unsplash.com/?utm_source=seneca&utm_medium=referral',
      image_url: `/v1/hero/image?v=${id}`,
    },
    settings: { enabled: true, theme: 'aerial' },
    themes: [{ id: 'aerial', label: 'Dark aerial landscapes' }],
    rotation_hours: 6,
    unsplash_configured: true,
    ...extra,
  };
}

function json(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = new MemoryStorage();
  let n = 0;
  globalThis.URL.createObjectURL = vi.fn(() => `blob:hero-${++n}`);
  globalThis.URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage = undefined;
});

describe('useHeroStore', () => {
  it('loads metadata and the image from the local server only', async () => {
    fetchMock
      .mockResolvedValueOnce(json(status('p1')))
      .mockResolvedValueOnce(new Response(new Blob(['jpeg']), { status: 200 }));
    const { useHeroStore } = await import('./hero');

    await useHeroStore.getState().load();

    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toEqual(['/v1/hero/current', '/v1/hero/image?v=p1']);
    expect(urls.some((u) => u.includes('unsplash.com'))).toBe(false);
    expect(useHeroStore.getState().imageUrl).toBe('blob:hero-1');
    expect(useHeroStore.getState().status?.photo.photographer).toBe('Ana');
  });

  it('sends the local API key with the image request', async () => {
    localStorage.setItem('openjarvis-settings', JSON.stringify({ apiKey: 'oj_sk_test' }));
    fetchMock
      .mockResolvedValueOnce(json(status('p1')))
      .mockResolvedValueOnce(new Response(new Blob(['jpeg']), { status: 200 }));
    const { useHeroStore } = await import('./hero');

    await useHeroStore.getState().load();

    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer oj_sk_test');
  });

  it('does not refetch the image while the photo is unchanged', async () => {
    fetchMock
      .mockResolvedValueOnce(json(status('p1')))
      .mockResolvedValueOnce(new Response(new Blob(['jpeg']), { status: 200 }))
      .mockResolvedValueOnce(json(status('p1')))
      .mockResolvedValueOnce(json(status('p2')))
      .mockResolvedValueOnce(new Response(new Blob(['jpeg2']), { status: 200 }));
    const { useHeroStore } = await import('./hero');

    await useHeroStore.getState().load();
    await useHeroStore.getState().load();
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await useHeroStore.getState().load();
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(useHeroStore.getState().imageUrl).toBe('blob:hero-2');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:hero-1');
  });

  it('keeps the page usable when the server has no hero route', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 404 }));
    const { useHeroStore } = await import('./hero');

    await useHeroStore.getState().load();

    expect(useHeroStore.getState().imageUrl).toBeNull();
    expect(useHeroStore.getState().error).toContain('404');
  });

  it('saves rotation and theme settings', async () => {
    fetchMock.mockResolvedValueOnce(json(status('p1', { settings: { enabled: false, theme: 'coast' } })));
    const { useHeroStore } = await import('./hero');

    await useHeroStore.getState().updateSettings({ enabled: false });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/v1/hero/settings');
    expect((init as RequestInit).method).toBe('PUT');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ enabled: false });
    expect(useHeroStore.getState().status?.settings).toEqual({ enabled: false, theme: 'coast' });
  });

  it('throws a readable error when saving fails', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 422 }));
    const { useHeroStore } = await import('./hero');

    await expect(useHeroStore.getState().updateSettings({ theme: 'nope' })).rejects.toThrow('422');
  });
});
