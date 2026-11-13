import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { splitDecimal } from '../components/ui/seneca';

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

beforeEach(() => {
  vi.resetModules();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = new MemoryStorage();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage = undefined;
});

describe('dark only settings', () => {
  it('drops a saved light or system theme and keeps everything else', async () => {
    localStorage.setItem(
      'openjarvis-settings',
      JSON.stringify({ theme: 'light', apiUrl: 'http://127.0.0.1:8000', temperature: 0.2 }),
    );
    const { useAppStore } = await import('./store');
    const settings = useAppStore.getState().settings as unknown as Record<string, unknown>;

    expect('theme' in settings).toBe(false);
    expect(settings.apiUrl).toBe('http://127.0.0.1:8000');
    expect(settings.temperature).toBe(0.2);
  });

  it('does not write a theme back when settings change', async () => {
    localStorage.setItem('openjarvis-settings', JSON.stringify({ theme: 'system' }));
    const { useAppStore } = await import('./store');

    useAppStore.getState().updateSettings({ fontSize: 'large' });

    const saved = JSON.parse(localStorage.getItem('openjarvis-settings') ?? '{}');
    expect(saved.theme).toBeUndefined();
    expect(saved.fontSize).toBe('large');
  });
});

describe('splitDecimal', () => {
  it('splits the decimals off so they can be dimmed', () => {
    expect(splitDecimal('48.2')).toEqual(['48', '.2']);
    expect(splitDecimal('214')).toEqual(['214', '']);
    expect(splitDecimal('$0.0412')).toEqual(['$0', '.0412']);
    expect(splitDecimal('Cool')).toEqual(['Cool', '']);
  });
});
