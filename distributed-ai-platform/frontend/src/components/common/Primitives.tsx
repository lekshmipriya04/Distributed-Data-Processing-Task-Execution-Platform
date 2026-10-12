import { ReactNode, useEffect, useState } from 'react';
import type { JobStatus } from '../../types';

export function StatusBadge({ status }: { status: JobStatus | string }) {
  const styles: Record<string, string> = {
    pending: 'bg-slate-100 text-slate-600 border-slate-200',
    running: 'bg-blue-50 text-blue-700 border-blue-200 animate-pulse',
    succeeded: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    failed: 'bg-rose-50 text-rose-700 border-rose-200',
    cancelled: 'bg-slate-100 text-slate-600 border-slate-200',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${styles[status] ?? styles.pending}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-white border border-slate-200/80 rounded-2xl p-6 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-slate-900 font-semibold text-lg mb-4">{children}</h3>;
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="px-4 py-2 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 transition-colors rounded-lg text-white font-medium text-sm shadow-sm hover:shadow disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-4 py-2 bg-slate-100 hover:bg-slate-200 active:bg-slate-300 text-slate-700 border border-slate-200 transition-colors rounded-lg font-medium text-sm disabled:opacity-40"
    >
      {children}
    </button>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl p-6 w-full max-w-sm border border-slate-200 shadow-xl">
        <h3 className="text-lg font-semibold text-slate-900 mb-2">{title}</h3>
        <p className="text-slate-600 text-sm mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <SecondaryButton onClick={onCancel}>Cancel</SecondaryButton>
          <PrimaryButton onClick={onConfirm}>Confirm</PrimaryButton>
        </div>
      </div>
    </div>
  );
}

export function TagInput({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
}) {
  // Accept one or many names at once: Enter or comma commits, and pasting a
  // comma/space/newline-separated list (e.g. "p0,p1,...,p63") adds them all.
  const addMany = (raw: string) => {
    const parts = raw.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    const merged = [...values];
    for (const p of parts) if (!merged.includes(p)) merged.push(p);
    onChange(merged);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const input = e.currentTarget;
    if ((e.key === 'Enter' || e.key === ',') && input.value.trim()) {
      e.preventDefault();
      addMany(input.value);
      input.value = '';
    }
    if (e.key === 'Backspace' && !input.value && values.length) {
      onChange(values.slice(0, -1));
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text');
    if (/[,\s]/.test(text)) {
      e.preventDefault();
      addMany(text);
      e.currentTarget.value = '';
    }
  };

  return (
    <div className="flex flex-wrap gap-2 bg-white rounded-lg px-3 py-2 border border-slate-300 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500">
      {values.map((v) => (
        <span key={v} className="flex items-center gap-1 bg-blue-50 text-blue-700 border border-blue-200 px-2 py-1 rounded-md text-sm font-medium">
          {v}
          <button onClick={() => onChange(values.filter((x) => x !== v))} className="text-blue-500 hover:text-blue-800 ml-0.5">
            ×
          </button>
        </span>
      ))}
      <input
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        placeholder={placeholder ?? 'type or paste a comma-separated list'}
        className="flex-1 min-w-[120px] bg-transparent outline-none text-slate-900 text-sm placeholder:text-slate-400"
      />
    </div>
  );
}

// Controlled numeric field backed by a string so editing is natural: typing
// over a leading 0 replaces it ("0" + "8" -> "8", not "08"), the field can be
// cleared mid-edit, and decimals like "0.05" type cleanly. Emits a number via
// onChange; falls back to `fallback` (or min, or 0) on blur when left empty.
export function NumberField({
  value,
  onChange,
  min,
  max,
  step,
  fallback,
  className = '',
}: {
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  fallback?: number;
  className?: string;
}) {
  const fmt = (n: number) => (Number.isFinite(n) ? String(n) : '');
  const [text, setText] = useState<string>(fmt(value));

  // Re-sync when the value is changed programmatically (reset, engine switch),
  // but don't clobber what the user is actively typing (same numeric value).
  useEffect(() => {
    const current = text.trim() === '' ? NaN : Number(text);
    if (current !== value) setText(fmt(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const clamp = (n: number) => {
    if (min !== undefined && n < min) return min;
    if (max !== undefined && n > max) return max;
    return n;
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Strip leading zeros that sit in front of another digit ("08" -> "8"),
    // but keep "0", "0." and "0.05" so decimals can be typed.
    const raw = e.target.value.replace(/^(-?)0+(?=\d)/, '$1');
    setText(raw);
    if (raw === '' || raw === '-' || raw === '.' || raw === '-.' || raw.endsWith('.')) return;
    const n = Number(raw);
    if (!Number.isNaN(n)) onChange(clamp(n));
  };

  const handleBlur = () => {
    if (text.trim() === '' || Number.isNaN(Number(text))) {
      const fb = fallback ?? min ?? 0;
      setText(fmt(fb));
      onChange(fb);
      return;
    }
    const n = clamp(Number(text));
    setText(fmt(n));
    onChange(n);
  };

  return (
    <input
      type="text"
      inputMode="decimal"
      value={text}
      step={step}
      onChange={handleChange}
      onBlur={handleBlur}
      className={className}
    />
  );
}
