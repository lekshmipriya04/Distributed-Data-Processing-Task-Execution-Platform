import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnsiText, parseAnsi } from './ansi';
import { CPPCOLOR_ERR } from './__fixtures__/logs';

describe('parseAnsi', () => {
  it('keeps plain text as one unstyled span', () => {
    expect(parseAnsi('hello')).toEqual([{ text: 'hello', style: {} }]);
  });

  it('applies colours, bold and reset', () => {
    const spans = parseAnsi('\u001b[1;31merror:\u001b[0m done');
    expect(spans).toEqual([
      { text: 'error:', style: { color: 'rgb(205, 49, 49)', fontWeight: 'bold' } },
      { text: ' done', style: {} },
    ]);
  });

  it('understands bright, 256-colour and true-colour codes', () => {
    expect(parseAnsi('\u001b[92mok')[0].style.color).toBe('rgb(35, 209, 139)');
    expect(parseAnsi('\u001b[38;5;196mx')[0].style.color).toBe('rgb(255, 0, 0)');
    expect(parseAnsi('\u001b[38;2;1;2;3mx')[0].style.color).toBe('rgb(1, 2, 3)');
    expect(parseAnsi('\u001b[48;5;232mx')[0].style.backgroundColor).toBe('rgb(8, 8, 8)');
  });

  it('drops other escape sequences and ignores nonsense values', () => {
    expect(parseAnsi('\u001b[2K\u001b]0;title\u0007a\u001b[38;5;999mb').map((s) => s.text).join('')).toBe('ab');
    expect(parseAnsi('\u001b[38;2;999;0;0mz')[0].style).toEqual({});
  });

  it('handles real compiler output without leaving escape codes', () => {
    const text = parseAnsi(CPPCOLOR_ERR).map((s) => s.text).join('');
    expect(text).not.toContain('\u001b');
    expect(text).toContain('error');
  });

  it('is linear on hostile input', () => {
    const hostile = '\u001b['.repeat(50_000) + 'x'.repeat(50_000);
    const started = Date.now();
    parseAnsi(hostile);
    expect(Date.now() - started).toBeLessThan(500);
  });
});

describe('AnsiText', () => {
  it('renders spans, and escapes HTML in the output', () => {
    const html = renderToStaticMarkup(<AnsiText>{'\u001b[31m<script>alert(1)</script>'}</AnsiText>);
    expect(html).toBe('<span style="color:rgb(205, 49, 49)">&lt;script&gt;alert(1)&lt;/script&gt;</span>');
  });
});
