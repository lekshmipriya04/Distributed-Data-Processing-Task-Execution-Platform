// Renders terminal colours (ANSI SGR escape codes) in task output. Small and
// dependency-free on purpose: output comes from user programs, so the parser
// does one linear pass and never interprets anything but colours and styles.

import type { CSSProperties } from 'react';

export interface AnsiSpan {
  text: string;
  style: CSSProperties;
}

// Standard and bright palette (the VS Code terminal's dark theme).
const PALETTE = [
  [0, 0, 0], [205, 49, 49], [13, 188, 121], [229, 229, 16],
  [36, 114, 200], [188, 63, 188], [17, 168, 205], [229, 229, 229],
  [102, 102, 102], [241, 76, 76], [35, 209, 139], [245, 245, 67],
  [59, 142, 234], [214, 112, 214], [41, 184, 219], [255, 255, 255],
];

const rgb = ([r, g, b]: number[]) => `rgb(${r}, ${g}, ${b})`;

/** Colour n of the 256-colour table. */
function color256(n: number): string | undefined {
  if (!Number.isInteger(n) || n < 0 || n > 255) return undefined;
  if (n < 16) return rgb(PALETTE[n]);
  if (n < 232) {
    const v = [Math.floor((n - 16) / 36), Math.floor(((n - 16) % 36) / 6), (n - 16) % 6];
    return rgb(v.map((c) => (c === 0 ? 0 : 55 + c * 40)));
  }
  const grey = 8 + (n - 232) * 10;
  return rgb([grey, grey, grey]);
}

interface State {
  fg?: string;
  bg?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
}

/** Applies one SGR parameter list (the numbers in ESC[...m) to the state. */
function apply(state: State, params: number[]): State {
  const s = { ...state };
  for (let i = 0; i < params.length; i++) {
    const p = params[i];
    if (p === 0) Object.keys(s).forEach((k) => delete s[k as keyof State]);
    else if (p === 1) s.bold = true;
    else if (p === 2) s.dim = true;
    else if (p === 3) s.italic = true;
    else if (p === 4) s.underline = true;
    else if (p === 22) s.bold = s.dim = false;
    else if (p === 23) s.italic = false;
    else if (p === 24) s.underline = false;
    else if (p >= 30 && p <= 37) s.fg = rgb(PALETTE[p - 30]);
    else if (p >= 90 && p <= 97) s.fg = rgb(PALETTE[p - 90 + 8]);
    else if (p === 39) s.fg = undefined;
    else if (p >= 40 && p <= 47) s.bg = rgb(PALETTE[p - 40]);
    else if (p >= 100 && p <= 107) s.bg = rgb(PALETTE[p - 100 + 8]);
    else if (p === 49) s.bg = undefined;
    else if ((p === 38 || p === 48) && params[i + 1] === 5) {
      const c = color256(params[i + 2]);
      if (p === 38) s.fg = c;
      else s.bg = c;
      i += 2;
    } else if ((p === 38 || p === 48) && params[i + 1] === 2) {
      const [r, g, b] = params.slice(i + 2, i + 5);
      const ok = [r, g, b].every((v) => Number.isInteger(v) && v >= 0 && v <= 255);
      if (ok) {
        if (p === 38) s.fg = rgb([r, g, b]);
        else s.bg = rgb([r, g, b]);
      }
      i += 4;
    }
  }
  return s;
}

function toStyle(s: State): CSSProperties {
  const style: CSSProperties = {};
  if (s.fg) style.color = s.fg;
  if (s.bg) style.backgroundColor = s.bg;
  if (s.bold) style.fontWeight = 'bold';
  if (s.dim) style.opacity = 0.7;
  if (s.italic) style.fontStyle = 'italic';
  if (s.underline) style.textDecoration = 'underline';
  return style;
}

// Any CSI sequence (colours are the "m" ones) or OSC sequence (window titles,
// hyperlinks), so nothing but text and colour reaches the page.
// eslint-disable-next-line no-control-regex
const ESCAPES = /\u001b\[([0-9;?]*)([ -/]*)([@-~])|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g;

/** Splits text into styled spans; unknown escape sequences are dropped. */
export function parseAnsi(text: string): AnsiSpan[] {
  const spans: AnsiSpan[] = [];
  let state: State = {};
  let last = 0;
  const push = (chunk: string) => {
    if (chunk) spans.push({ text: chunk, style: toStyle(state) });
  };
  for (const m of text.matchAll(ESCAPES)) {
    push(text.slice(last, m.index));
    last = (m.index ?? 0) + m[0].length;
    if (m[3] === 'm' && !m[2]) {
      const params = (m[1] || '0').split(';').map((x) => (x === '' ? 0 : Number(x)));
      state = apply(state, params);
    }
  }
  push(text.slice(last));
  return spans;
}

/** Text with its terminal colours, rendered as React spans (never as HTML). */
export function AnsiText({ children }: { children: string }) {
  return (
    <>
      {parseAnsi(children).map((s, i) =>
        Object.keys(s.style).length ? (
          <span key={i} style={s.style}>
            {s.text}
          </span>
        ) : (
          s.text
        ),
      )}
    </>
  );
}
