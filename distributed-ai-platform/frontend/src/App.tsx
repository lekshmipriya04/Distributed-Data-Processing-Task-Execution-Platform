import { useState, useEffect } from 'react';
import { Activity, PlayCircle, Terminal, Code2 } from 'lucide-react';
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';

import { ToastProvider } from './context/ToastContext';
import { AccountMenu } from './components/common/AccountMenu';
import { SSHExecutorPage } from './pages/SSHExecutorPage';
import { TrainPage } from './pages/TrainPage';
import { TaskExecutorPage } from './pages/TaskExecutorPage';

const NAV = [
  { to: '/ssh', label: 'SSH Connection', icon: <Terminal size={20} /> },
  { to: '/train', label: 'Train Job', icon: <PlayCircle size={20} /> },
  { to: '/execute', label: 'Task Executor', icon: <Code2 size={20} /> },
];

const TITLES: Record<string, string> = {
  '/ssh': 'SSH Connection',
  '/train': 'Train Job',
  '/execute': 'Task Executor',
};

const HEALTH_POLL_MS = 15000;

function Layout() {
  const location = useLocation();
  const [gatewayOk, setGatewayOk] = useState<boolean | null>(null);

  // Re-checked regularly so the badge follows the gateway going down or up.
  useEffect(() => {
    let active = true;
    const check = () =>
      fetch('/health', { cache: 'no-store' })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => active && setGatewayOk(data?.status === 'ok'))
        .catch(() => active && setGatewayOk(false));
    check();
    const timer = window.setInterval(check, HEALTH_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const pageTitle = TITLES[location.pathname] ?? 'Distributed Compute';

  return (
    <div className="flex flex-col md:flex-row h-screen bg-slate-50 text-slate-800 font-sans selection:bg-blue-500/20">
      <aside className="hidden md:flex w-64 shrink-0 bg-white border-r border-slate-200 p-6 flex-col shadow-sm z-10">
        <Brand />
        <nav className="flex-1 space-y-2" aria-label="Main">
          {NAV.map((item) => (
            <NavItem key={item.to} to={item.to} icon={item.icon} label={item.label} active={location.pathname === item.to} />
          ))}
        </nav>
        <p className="text-xs text-slate-500" data-testid="app-version">
          Version {__APP_VERSION__}
        </p>
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden relative bg-slate-50 min-w-0">
        <header className="border-b border-slate-200 bg-white/90 backdrop-blur-md sticky top-0 z-10">
          <div className="h-16 flex items-center justify-between gap-3 px-4 md:px-8">
            <div className="md:hidden">
              <Brand compact />
            </div>
            <h2 className="hidden md:block text-xl font-semibold tracking-tight text-slate-800">{pageTitle}</h2>
            <div className="flex items-center gap-2 md:gap-3">
              <AccountMenu />
              <div
                role="status"
                data-testid="gateway-status"
                className={`flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-full border ${
                  gatewayOk
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : gatewayOk === null
                      ? 'bg-slate-50 text-slate-500 border-slate-200'
                      : 'bg-rose-50 text-rose-700 border-rose-200'
                }`}
              >
                <div
                  className={`w-2 h-2 rounded-full ${gatewayOk ? 'bg-emerald-500 animate-pulse' : gatewayOk === null ? 'bg-slate-400' : 'bg-rose-500'}`}
                />
                <span className="hidden sm:inline">Gateway:</span> {gatewayOk ? 'Online' : gatewayOk === null ? 'Checking' : 'Offline'}
              </div>
            </div>
          </div>
          <nav className="md:hidden flex gap-1 px-2 pb-2 overflow-x-auto" aria-label="Main">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm whitespace-nowrap ${
                  location.pathname === item.to ? 'bg-blue-50 text-blue-700 font-semibold' : 'text-slate-600'
                }`}
              >
                {item.icon}
                {item.label}
              </Link>
            ))}
          </nav>
        </header>

        <main className="flex-1 overflow-auto p-4 md:p-8 relative z-0">
          <Routes>
            <Route path="/" element={<Navigate to="/ssh" replace />} />
            <Route path="/ssh" element={<SSHExecutorPage />} />
            <Route path="/train" element={<TrainPage />} />
            <Route path="/execute" element={<TaskExecutorPage />} />
            <Route path="*" element={<Navigate to="/ssh" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`flex items-center gap-3 ${compact ? '' : 'mb-10'}`}>
      <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center shadow-md shadow-blue-500/20">
        <Activity className="w-5 h-5 text-white" />
      </div>
      <h1 className={`${compact ? 'text-base' : 'text-lg'} font-bold text-slate-900 tracking-tight`}>Distributed Compute</h1>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <Layout />
      </BrowserRouter>
    </ToastProvider>
  );
}

function NavItem({ icon, label, to, active }: { icon: React.ReactNode; label: string; to: string; active?: boolean }) {
  return (
    <Link
      to={to}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group ${
        active
          ? 'bg-blue-50 text-blue-600 font-semibold shadow-sm border border-blue-100'
          : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
      }`}
    >
      <span className={`${active ? 'text-blue-600' : 'text-slate-500 group-hover:text-slate-600'} transition-colors duration-200`}>
        {icon}
      </span>
      <span className="text-sm">{label}</span>
    </Link>
  );
}
