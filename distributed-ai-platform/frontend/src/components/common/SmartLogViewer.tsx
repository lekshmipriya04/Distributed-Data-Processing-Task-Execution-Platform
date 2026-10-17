import { useMemo, useState } from 'react';
import Ansi from 'ansi-to-react';
import { AlertTriangle, Check, Clock, Copy, Search, TimerOff } from 'lucide-react';
import { extractKeyError, filterLines, stripAnsi, type KeyError } from './logParsing';

type Tab = 'all' | 'errors' | 'output';

interface Props {
  stdout?: string | null;
  stderr?: string | null;
  exitCode?: number | null;
  status?: string;
  durationMs?: number | null;
  /** Shown in the header, e.g. the language. */
  label?: string;
}

const KIND_TITLE: Record<KeyError['kind'], string> = {
  compile: 'Compile error',
  runtime: 'Runtime error',
  timeout: 'Timed out',
  exit: 'Failed',
};

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API blocked (http origin, permissions): fall back to a selection copy.
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}

function formatDuration(ms?: number | null) {
  if (ms == null) return null;
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  const onClick = async () => {
    setState((await copyText(text)) ? 'done' : 'failed');
    window.setTimeout(() => setState('idle'), 1500);
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      {state === 'done' ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
      {state === 'done' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}
    </button>
  );
}

/** Task output with the root error pinned on top, ANSI colours, filters and copy buttons. */
export function SmartLogViewer({ stdout, stderr, exitCode, status, durationMs, label }: Props) {
  const [tab, setTab] = useState<Tab>('all');
  const [query, setQuery] = useState('');
  const out = stdout ?? '';
  const err = stderr ?? '';
  const keyError = useMemo(() => extractKeyError(err, exitCode, out), [err, exitCode, out]);

  const errorText = keyError
    ? [keyError.file ? `${keyError.file}${keyError.line ? `:${keyError.line}` : ''}${keyError.column ? `:${keyError.column}` : ''}` : '', keyError.message]
        .filter(Boolean)
        .join(': ')
    : '';
  const fullLog = [out && `--- stdout ---\n${stripAnsi(out)}`, err && `--- stderr ---\n${stripAnsi(err)}`].filter(Boolean).join('\n');

  const sections = useMemo(() => {
    const pick: Array<{ kind: 'stdout' | 'stderr'; text: string }> = [];
    if (tab !== 'errors' && out) pick.push({ kind: 'stdout', text: out });
    if (tab !== 'output' && err) pick.push({ kind: 'stderr', text: err });
    return pick.map((s) => ({ ...s, lines: filterLines(s.text.replace(/\n+$/, ''), query) }));
  }, [tab, out, err, query]);
  const shownLines = sections.reduce((n, s) => n + (query ? s.lines.length : 0), 0);

  if (!out && !err && exitCode == null) {
    return status && ['pending', 'running'].includes(status) ? (
      <p className="px-4 py-3 text-xs text-slate-400 italic">Waiting for output…</p>
    ) : null;
  }

  const tabs: Array<{ id: Tab; label: string; disabled: boolean }> = [
    { id: 'all', label: 'All logs', disabled: false },
    { id: 'errors', label: 'Errors only', disabled: !err },
    { id: 'output', label: 'Output only', disabled: !out },
  ];

  return (
    <div className="border-t border-slate-200">
      {/* Header: exit code, time, copy */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-white">
        {exitCode != null && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${
              exitCode === 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
            }`}
          >
            Exit {exitCode}
          </span>
        )}
        {formatDuration(durationMs) && (
          <span className="inline-flex items-center gap-1 text-xs text-slate-500 tabular-nums">
            <Clock size={12} /> {formatDuration(durationMs)}
          </span>
        )}
        {label && <span className="text-xs text-slate-400">{label}</span>}
        <span className="ml-auto flex gap-2">
          {keyError && <CopyButton text={errorText} label="Copy error" />}
          <CopyButton text={fullLog} label="Copy logs" />
        </span>
      </div>

      {/* Pinned root error */}
      {keyError && (
        <div className="mx-4 mb-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5" role="alert">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-rose-700">
            {keyError.kind === 'timeout' ? <TimerOff size={14} /> : <AlertTriangle size={14} />}
            {KIND_TITLE[keyError.kind]}
            {keyError.file && (
              <span className="normal-case tracking-normal font-mono font-medium text-rose-600 break-all">
                {keyError.file}
                {keyError.line ? `:${keyError.line}` : ''}
                {keyError.column ? `:${keyError.column}` : ''}
              </span>
            )}
          </div>
          <p className="mt-1 font-mono text-sm text-rose-900 break-words">{keyError.message}</p>
        </div>
      )}

      {/* View modes and filter */}
      <div className="flex flex-wrap items-center gap-2 px-4 pb-2">
        <div className="inline-flex rounded-md border border-slate-300 p-0.5 bg-slate-50" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              disabled={t.disabled}
              onClick={() => setTab(t.id)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40 ${
                tab === t.id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="relative ml-auto">
          <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter lines"
            aria-label="Filter log lines"
            className="w-44 rounded-md border border-slate-300 bg-white py-1 pl-7 pr-2 text-xs outline-none focus:ring-2 focus:ring-blue-500"
          />
        </label>
        {query && <span className="text-xs text-slate-500 tabular-nums">{shownLines} matching</span>}
      </div>

      {/* Terminal */}
      <div className="max-h-80 overflow-auto bg-[#1e1e1e] px-4 py-3 font-mono text-xs leading-relaxed">
        {sections.length === 0 || sections.every((s) => s.lines.every((l) => l === '')) ? (
          <span className="text-slate-500">{query ? 'No lines match.' : 'No output.'}</span>
        ) : (
          sections.map((s) => (
            <div key={s.kind} className={s.kind === 'stderr' ? 'text-rose-300' : 'text-slate-200'}>
              {tab === 'all' && sections.length > 1 && (
                <div className="mb-1 mt-2 first:mt-0 text-[10px] uppercase tracking-wider text-slate-500">{s.kind}</div>
              )}
              <pre className="whitespace-pre-wrap break-words">
                <Ansi>{s.lines.join('\n')}</Ansi>
              </pre>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
