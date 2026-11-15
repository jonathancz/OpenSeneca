import { describe, expect, it } from 'vitest';
import { BASE_COLORS, parseAnsi, parseAnsiLine, xterm256 } from './ansi';

describe('ansi', () => {
  it('keeps plain text as one run', () => {
    expect(parseAnsiLine('hello').runs).toEqual([{ text: 'hello' }]);
  });

  it('applies base, bright, 256 and true colours and resets', () => {
    const { runs } = parseAnsiLine('\x1b[31mred\x1b[0m plain \x1b[1;92mok\x1b[22;39m \x1b[38;5;211mpink\x1b[38;2;1;2;3mrgb');
    expect(runs).toEqual([
      { text: 'red', fg: BASE_COLORS[1] },
      { text: ' plain ' },
      { text: 'ok', bold: true, fg: BASE_COLORS[10] },
      { text: ' ' },
      { text: 'pink', fg: xterm256(211) },
      { text: 'rgb', fg: 'rgb(1, 2, 3)' },
    ]);
    expect(xterm256(16)).toBe('rgb(0, 0, 0)');
    expect(xterm256(232)).toBe('rgb(8, 8, 8)');
  });

  it('drops hyperlinks, cursor moves and control characters', () => {
    const line = '\x1b]8;id=1;https://example.com\x1b\\PR #7\x1b]8;;\x1b\\ \x1b[2K\x1b[1Gdone\x07';
    expect(parseAnsiLine(line).runs.map((r) => r.text).join('')).toBe('PR #7 done');
  });

  it('carries colour across lines like a terminal', () => {
    const rows = parseAnsi(['\x1b[32mstart', 'still green\x1b[0m', 'plain']);
    expect(rows[1]).toEqual([{ text: 'still green', fg: BASE_COLORS[2] }]);
    expect(rows[2]).toEqual([{ text: 'plain' }]);
  });
});
