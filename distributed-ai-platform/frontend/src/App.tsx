import { useState, useEffect } from 'react';
import { Activity, PlayCircle, Terminal, Code2 } from 'lucide-react';
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom';

import { ClusterConfigProvider } from './context/ClusterConfigContext';
import { ToastProvider } from './context/ToastContext';
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

function Layout() {
  const location = useLocation();
  const [healthStatus, setHealthStatus] = useState<{ status?: string } | null>(null);

  useEffect(() => {
    fetch('/health')
      .then((res) => res.json())
      .then((data) => setHealthStatus(data))
      .catch((err) => setHealthStatus({ status: 'error', ...err }));
  }, []);

  const pageTitle = TITLES[location.pathname] ?? 'Distributed Compute';

  return (
    <div className="flex h-screen bg-slate-50 text-slate-800 font-sans selection:bg-blue-500/20">
      <div className="w-64 bg-white border-r border-slate-200 p-6 flex flex-col shadow-sm z-10">
        <div className="flex items-center gap-3 mb-10">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center shadow-md shadow-blue-500/20">
            <Activity className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-lg font-bold text-slate-900 tracking-tight">
            Distributed Compute
          </h1>
        </div>

        <nav className="flex-1 space-y-2">
          {NAV.map((item) => (
            <NavItem key={item.to} to={item.to} icon={item.icon} label={item.label} active={location.pathname === item.to} />
          ))}
        </nav>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden relative bg-slate-50">
        <header className="h-16 border-b border-slate-200 bg-white/90 backdrop-blur-md flex items-center justify-between px-8 sticky top-0 z-10">
          <h2 className="text-xl font-semibold tracking-tight text-slate-800">{pageTitle}</h2>
          <div className="flex items-center gap-4">
            <div
              className={`flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-full border ${
                healthStatus?.status === 'ok'
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : 'bg-rose-50 text-rose-700 border-rose-200'
              }`}
            >
              <div className={`w-2 h-2 rounded-full ${healthStatus?.status === 'ok' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`} />
              Gateway: {healthStatus?.status === 'ok' ? 'Online' : 'Offline'}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-auto p-8 relative z-0">
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

export default function App() {
  return (
    <ClusterConfigProvider>
      <ToastProvider>
        <BrowserRouter>
          <Layout />
        </BrowserRouter>
      </ToastProvider>
    </ClusterConfigProvider>
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
      <span className={`${active ? 'text-blue-600' : 'text-slate-400 group-hover:text-slate-600'} transition-colors duration-200`}>
        {icon}
      </span>
      <span className="text-sm">{label}</span>
    </Link>
  );
}
