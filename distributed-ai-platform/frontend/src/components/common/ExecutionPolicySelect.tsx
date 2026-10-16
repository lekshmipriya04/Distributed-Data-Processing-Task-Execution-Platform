import { EXECUTION_POLICIES, type ExecutionPolicy } from '../../api/ssh';

/** How hard a job may lean on the lent slice of each worker. */
export function ExecutionPolicySelect({
  value,
  onChange,
  className,
}: {
  value: ExecutionPolicy;
  onChange: (p: ExecutionPolicy) => void;
  className?: string;
}) {
  const current = EXECUTION_POLICIES.find((p) => p.value === value);
  return (
    <label className="text-sm block">
      <span className="text-xs text-slate-500 font-medium block mb-1">Execution policy</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as ExecutionPolicy)}
        className={className ?? 'w-full border border-slate-300 rounded-lg p-2 text-sm bg-white'}
        title={current?.hint}
      >
        {EXECUTION_POLICIES.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
    </label>
  );
}
