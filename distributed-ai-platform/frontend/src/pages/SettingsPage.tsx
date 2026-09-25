import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Card, SectionTitle, PrimaryButton, SecondaryButton } from '../components/common/Primitives';

export function SettingsPage() {
  const { token, setToken, logout } = useAuth();
  const [draft, setDraft] = useState('');

  return (
    <div className="max-w-xl space-y-6">
      <Card>
        <SectionTitle>Authentication</SectionTitle>
        <p className="text-sm text-slate-400 mb-4">
          This backend has no login endpoint (see PRD §9.11) — tokens must be minted separately
          with the same <span className="font-mono">JWT_SECRET</span> configured in{' '}
          <span className="font-mono">config/.env</span>, e.g. via the project's{' '}
          <span className="font-mono">generate_token.py</span> script. Paste that token here for
          local development.
        </p>

        {token ? (
          <div>
            <p className="text-emerald-400 text-sm mb-3">A token is currently set.</p>
            <SecondaryButton onClick={logout}>Clear token</SecondaryButton>
          </div>
        ) : (
          <div className="flex gap-3">
            <input
              className="flex-1 bg-slate-800 rounded-lg px-3 py-2 text-white border border-slate-700 focus:border-indigo-500 outline-none font-mono text-sm"
              placeholder="Paste JWT here"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <PrimaryButton onClick={() => setToken(draft)} disabled={!draft}>
              Save
            </PrimaryButton>
          </div>
        )}
      </Card>
    </div>
  );
}
