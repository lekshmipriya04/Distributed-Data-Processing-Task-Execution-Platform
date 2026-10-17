import { useEffect, useState } from 'react';
import { KeyRound, LogOut, UserRound } from 'lucide-react';
import { AUTH_REQUIRED_EVENT, clearToken, errorMessage, getToken, isExpired, setToken, tokenInfo } from '../../api/client';
import { sshApi } from '../../api/ssh';
import { PrimaryButton, SecondaryButton } from './Primitives';

/**
 * Who is signed in, and a dialog to sign in with a token or sign out. The
 * token lives in this origin's localStorage, so every host:port of the
 * dashboard can be a different user.
 */
export function AccountMenu() {
  const [info, setInfo] = useState(() => tokenInfo(getToken()));
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const onAuthRequired = () => {
      const current = tokenInfo(getToken());
      setReason(
        current && isExpired(current)
          ? 'Your sign-in has expired. Paste a new token.'
          : current
            ? 'The server did not accept your token. Paste a valid one.'
            : 'This server requires sign-in.',
      );
      setOpen(true);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
  }, []);

  // Escape closes the dialog.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const expired = isExpired(info);

  const signIn = async () => {
    const token = draft.trim();
    if (!tokenInfo(token)) {
      setError('That is not a platform token. It has three parts separated by dots (xxxxx.yyyyy.zzzzz).');
      return;
    }
    if (isExpired(tokenInfo(token))) {
      setError('That token has expired. Create a new one with generate_token.py.');
      return;
    }
    const previous = getToken();
    setToken(token);
    setChecking(true);
    try {
      await sshApi.listNodes();
      // Reload so every page fetches its data as the new user.
      window.location.reload();
    } catch (e) {
      if (previous) setToken(previous);
      else clearToken();
      setError(`The server rejected this token: ${errorMessage(e)}`);
    } finally {
      setChecking(false);
    }
  };

  const signOut = () => {
    clearToken();
    setInfo(null);
    window.location.reload();
  };

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setReason(null);
          setError(null);
          setDraft('');
          setOpen(true);
        }}
        className={`flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-full border transition-colors ${
          expired
            ? 'bg-amber-50 text-amber-800 border-amber-200'
            : 'bg-white text-slate-700 border-slate-200 hover:border-slate-300'
        }`}
        aria-label={info ? `Account: signed in as ${info.sub}` : 'Account: local user, sign in'}
        data-testid="account-button"
      >
        <UserRound size={15} />
        <span className="max-w-[10rem] truncate">{info ? (expired ? `${info.sub} (expired)` : info.sub) : 'Local user'}</span>
      </button>

      {open && (
        <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true" aria-labelledby="account-title">
          <div className="bg-white rounded-2xl p-6 w-full max-w-md border border-slate-200 shadow-xl">
            <h3 id="account-title" className="text-lg font-semibold text-slate-900 mb-1 flex items-center gap-2">
              <KeyRound size={18} className="text-blue-600" /> {info ? 'Account' : 'Sign in'}
            </h3>
            {reason && <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 my-3">{reason}</p>}
            {info ? (
              <p className="text-sm text-slate-600 mb-4">
                Signed in as <span className="font-semibold text-slate-900">{info.sub}</span>
                {info.expiresAt ? (expired ? ' · expired' : ` · until ${new Date(info.expiresAt).toLocaleString()}`) : ''}.
              </p>
            ) : (
              <p className="text-sm text-slate-600 mb-4">
                You are the local user. On a shared or production server, paste the token your administrator created with{' '}
                <code className="font-mono text-xs bg-slate-100 rounded px-1">python generate_token.py --sub &lt;name&gt;</code>.
              </p>
            )}
            <label className="text-sm block">
              <span className="text-xs text-slate-500 font-medium block mb-1">{info ? 'Switch to another token' : 'Token'}</span>
              <textarea
                autoFocus
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setError(null);
                }}
                rows={3}
                spellCheck={false}
                autoComplete="off"
                aria-label="Token"
                placeholder="eyJhbGciOiJIUzI1NiIs…"
                className="w-full font-mono text-xs border border-slate-300 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            {error && (
              <p className="text-sm text-rose-700 mt-2" role="alert">
                {error}
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2 mt-5">
              {info && (
                <SecondaryButton onClick={signOut}>
                  <span className="flex items-center gap-1.5">
                    <LogOut size={14} /> Sign out
                  </span>
                </SecondaryButton>
              )}
              <SecondaryButton onClick={() => setOpen(false)}>Close</SecondaryButton>
              <PrimaryButton onClick={signIn} disabled={checking || draft.trim() === ''}>
                {checking ? 'Checking…' : 'Sign in'}
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
