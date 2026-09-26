import React, { useState, useEffect } from 'react';
import { Activity, PlayCircle, LayoutDashboard } from 'lucide-react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';

import { AuthProvider } from './context/AuthContext';
import { ClusterConfigProvider } from './context/ClusterConfigContext';
import { ToastProvider } from './context/ToastContext';
import { TrainPage } from './pages/TrainPage';
import { WorkersPage } from './pages/WorkersPage';
import { ResourceRequestsPage } from './pages/ResourceRequestsPage';
import { ModelDashboardPage } from './pages/ModelDashboardPage';
import { Server, ClipboardList, BarChart2 } from 'lucide-react';

function Dashboard() {
  const [metrics, setMetrics] = useState({
    active_models: 'Loading...',
    storage_used: 'Loading...',
    avg_latency: 'Loading...',
  });

  useEffect(() => {
    fetch('/api/v1/dashboard/dashboard')
      .then((res) => res.json())
      .then((data) => setMetrics(data))
      .catch((err) => console.error(err));
  }, []);

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <StatCard title="Active Models" value={metrics.active_models} subtitle="+2 this week" />
      <StatCard title="Storage Used" value={metrics.storage_used} subtitle="65% capacity" />
      <StatCard title="Avg Latency" value={metrics.avg_latency} subtitle="p95: 120ms" />
    </div>
  );
}

function Layout() {
  const location = useLocation();
  const [healthStatus, setHealthStatus] = useState<any>(null);

  useEffect(() => {
    fetch('/health')
      .then((res) => res.json())
      .then((data) => setHealthStatus(data))
      .catch((err) => setHealthStatus({ status: 'error', error: err.message }));
  }, []);

  const currentPath = location.pathname;
  const pageTitle =
    currentPath === '/'
      ? 'Dashboard'
      : currentPath === '/models'
      ? 'Model Metrics & Evaluation'
      : currentPath === '/resources'
      ? 'Resource Requests'
      : currentPath.substring(1).charAt(0).toUpperCase() + currentPath.substring(2);

  return (
    <div className="flex h-screen bg-slate-50 text-slate-800 font-sans selection:bg-blue-500/20">
      {/* Sidebar */}
      <div className="w-64 bg-white border-r border-slate-200 p-6 flex flex-col shadow-sm z-10">
        <div className="flex items-center gap-3 mb-10">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center shadow-md shadow-blue-500/20">
            <Activity className="w-5 h-5 text-white" />
          </div>
          <h1 className="text-lg font-bold text-slate-900 tracking-tight">
            AI ML Training Platform
          </h1>
        </div>

        <nav className="flex-1 space-y-2">
          <NavItem to="/" icon={<LayoutDashboard size={20} />} label="Dashboard" active={currentPath === '/'} />
          <NavItem to="/train" icon={<PlayCircle size={20} />} label="Train Job" active={currentPath === '/train'} />
          <NavItem to="/models" icon={<BarChart2 size={20} />} label="Model Metrics" active={currentPath === '/models'} />
          <NavItem to="/workers" icon={<Server size={20} />} label="Workers" active={currentPath === '/workers'} />
          <NavItem to="/resources" icon={<ClipboardList size={20} />} label="Resource Requests" active={currentPath === '/resources'} />
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden relative bg-slate-50">
        <header className="h-16 border-b border-slate-200 bg-white/90 backdrop-blur-md flex items-center justify-between px-8 sticky top-0 z-10">
          <h2 className="text-xl font-semibold tracking-tight text-slate-800 capitalize">{pageTitle}</h2>
          <div className="flex items-center gap-4">
            <div
              className={`flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-full border ${healthStatus?.status === 'ok'
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : 'bg-rose-50 text-rose-700 border-rose-200'
                }`}
            >
              <div className={`w-2 h-2 rounded-full ${healthStatus?.status === 'ok' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`}></div>
              Gateway: {healthStatus?.status === 'ok' ? 'Online' : 'Offline'}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-auto p-8 relative z-0">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/train" element={<TrainPage />} />
            <Route path="/models" element={<ModelDashboardPage />} />
            <Route path="/workers" element={<WorkersPage />} />
            <Route path="/resources" element={<ResourceRequestsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ClusterConfigProvider>
        <ToastProvider>
          <BrowserRouter>
            <Layout />
          </BrowserRouter>
        </ToastProvider>
      </ClusterConfigProvider>
    </AuthProvider>
  );
}

function NavItem({ icon, label, to, active }: { icon: React.ReactNode; label: string; to: string; active?: boolean }) {
  return (
    <Link
      to={to}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group ${active
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

function StatCard({ title, value, subtitle }: { title: string; value: string; subtitle: string }) {
  return (
    <div className="bg-white border border-slate-200 p-6 rounded-2xl shadow-sm hover:shadow-md hover:border-blue-300 transition-all duration-200 group">
      <h3 className="text-slate-500 text-sm font-medium mb-2">{title}</h3>
      <div className="text-3xl font-bold text-slate-900 tracking-tight group-hover:text-blue-600 transition-colors">{value}</div>
      <p className="text-slate-400 text-xs mt-2 font-medium">{subtitle}</p>
    </div>
  );
}
