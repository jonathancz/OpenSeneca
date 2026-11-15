/**
 * Terminal text with ANSI colours, turned into styled runs for a view only
 * mirror. Only SGR (colour and weight) codes are honoured; cursor moves, OSC
 * hyperlinks and every other control sequence are dropped.
 */

export interface AnsiRun {
  text: string;
  fg?: string;
  bg?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
  inverse?: boolean;
}

/** The 16 base colours, tuned to read on Seneca's near black surface. */
export const BASE_COLORS = [
  '#3a3a3e', '#f0524f', '#6cc48c', '#e2b24a', '#5ebaff', '#a280ff', '#4fd1c5', '#d6d6d1',
  '#7f7f7a', '#ff7b78', '#8fe0aa', '#f2cb6b', '#82caff', '#b79dff', '#7ae5da', '#f5f5f3',
];

/** xterm 256 colour index to a CSS colour. */
export function xterm256(n: number): string {
  if (n < 16) return BASE_COLORS[n];
  if (n < 232) {
    const i = n - 16;
    const level = [0, 95, 135, 175, 215, 255];
    const r = level[Math.floor(i / 36)];
    const g = level[Math.floor(i / 6) % 6];
    const b = level[i % 6];
    return `rgb(${r}, ${g}, ${b})`;
  }
  const v = 8 + (n - 232) * 10;
  return `rgb(${v}, ${v}, ${v})`;
}

type Style = Omit<AnsiRun, 'text'>;

function applySgr(style: Style, params: number[]): Style {
  const s: Style = { ...style };
  if (params.length === 0) params = [0];
  for (let i = 0; i < params.length; i++) {
    const p = params[i];
    if (p === 0) {
      for (const k of Object.keys(s)) delete (s as Record<string, unknown>)[k];
    } else if (p === 1) s.bold = true;
    else if (p === 2) s.dim = true;
    else if (p === 3) s.italic = true;
    else if (p === 4) s.underline = true;
    else if (p === 7) s.inverse = true;
    else if (p === 22) {
      delete s.bold;
      delete s.dim;
    } else if (p === 23) delete s.italic;
    else if (p === 24) delete s.underline;
    else if (p === 27) delete s.inverse;
    else if (p >= 30 && p <= 37) s.fg = BASE_COLORS[p - 30];
    else if (p >= 90 && p <= 97) s.fg = BASE_COLORS[p - 90 + 8];
    else if (p >= 40 && p <= 47) s.bg = BASE_COLORS[p - 40];
    else if (p >= 100 && p <= 107) s.bg = BASE_COLORS[p - 100 + 8];
    else if (p === 39) delete s.fg;
    else if (p === 49) delete s.bg;
    else if (p === 38 || p === 48) {
      const key = p === 38 ? 'fg' : 'bg';
      if (params[i + 1] === 5 && params[i + 2] != null) {
        s[key] = xterm256(params[i + 2]);
        i += 2;
      } else if (params[i + 1] === 2 && params[i + 4] != null) {
        s[key] = `rgb(${params[i + 2]}, ${params[i + 3]}, ${params[i + 4]})`;
        i += 4;
      }
    }
  }
  return s;
}

// CSI (ESC [ ... final byte), OSC (ESC ] ... BEL or ESC \), and lone ESC pairs.
const ESCAPES = /\x1b\[([0-9;?]*)([@-~])|\x1b\][\s\S]*?(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

/** One line of terminal output as styled runs. `style` carries over between lines. */
export function parseAnsiLine(line: string, style: Style = {}): { runs: AnsiRun[]; style: Style } {
  const runs: AnsiRun[] = [];
  let cur: Style = style;
  let last = 0;
  const push = (text: string) => {
    if (!text) return;
    const clean = text.replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '');
    if (clean) runs.push({ text: clean, ...cur });
  };
  for (const m of line.matchAll(ESCAPES)) {
    push(line.slice(last, m.index));
    last = (m.index ?? 0) + m[0].length;
    if (m[2] === 'm') {
      const params = (m[1] || '')
        .split(';')
        .filter((x) => x !== '' && !x.startsWith('?'))
        .map((x) => Number(x));
      cur = applySgr(cur, params.filter((x) => Number.isFinite(x)));
    }
  }
  push(line.slice(last));
  return { runs, style: cur };
}

/** Every line, with styles carried from one line to the next as a terminal would. */
export function parseAnsi(lines: string[]): AnsiRun[][] {
  let style: Style = {};
  return lines.map((l) => {
    const out = parseAnsiLine(l, style);
    style = out.style;
    return out.runs;
  });
}
