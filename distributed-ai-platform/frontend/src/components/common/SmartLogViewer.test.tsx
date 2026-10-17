import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { SmartLogViewer } from './SmartLogViewer';
import { CPPCOLOR_ERR, PY_ERR } from './__fixtures__/logs';

describe('SmartLogViewer', () => {
  it('pins the root error and renders colored compiler output without raw escape codes', () => {
    const html = renderToStaticMarkup(<SmartLogViewer stderr={CPPCOLOR_ERR} exitCode={1} durationMs={1520} label="cpp" />);
    expect(html).toContain('Compile error');
    expect(html).toContain('m.cpp:2:12');
    expect(html).toContain('expected &#x27;;&#x27; at end of declaration');
    expect(html).toContain('Exit 1');
    expect(html).toContain('1.52 s');
    expect(html).toContain('Copy error');
    expect(html).not.toContain('\u001b[');
    expect(html).toMatch(/style="[^"]*color:\s*rgb/);
  });

  it('shows a Python traceback with its innermost frame', () => {
    const html = renderToStaticMarkup(<SmartLogViewer stderr={PY_ERR} exitCode={1} />);
    expect(html).toContain('Runtime error');
    expect(html).toContain('ZeroDivisionError: division by zero');
    expect(html).toContain('t.py:2');
  });

  it('shows clean output without an error banner on success', () => {
    const html = renderToStaticMarkup(<SmartLogViewer stdout={'Training complete\n'} exitCode={0} />);
    expect(html).toContain('Exit 0');
    expect(html).toContain('Training complete');
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('Copy error');
  });

  it('renders nothing for a finished task with no output', () => {
    expect(renderToStaticMarkup(<SmartLogViewer status="completed" />)).toBe('');
    expect(renderToStaticMarkup(<SmartLogViewer status="running" />)).toContain('Waiting for output');
  });
});
