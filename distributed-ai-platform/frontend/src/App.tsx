import React, { useState, useEffect } from 'react';

import {
  BrowserRouter,
  Routes,
  Route,
  Link,
  useLocation,
} from 'react-router-dom';

import { AuthProvider } from './context/AuthContext';
import { ClusterConfigProvider } from './context/ClusterConfigContext';
import { ToastProvider } from './context/ToastContext';
import { TrainPage } from './pages/TrainPage';

import { WorkersPage } from './pages/WorkersPage';
import { ResourceRequestsPage } from './pages/ResourceRequestsPage';


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

      <StatCard
        title="Active Models"
        value={metrics.active_models}
      />

      <StatCard
        title="Storage Used"
        value={metrics.storage_used}
      />

      <StatCard
        title="Avg Latency"
        value={metrics.avg_latency}
      />

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
      .catch((err) =>
        setHealthStatus({
          status: 'error',
          error: err.message,
        })
      );
  }, []);

  const currentPath = location.pathname;

  const pageTitle =
    currentPath === '/'
      ? 'Dashboard'
      : currentPath.substring(1).charAt(0).toUpperCase() +
      currentPath.substring(2);

  return (
    <div className="flex flex-col h-screen bg-slate-50 text-slate-800 font-sans selection:bg-blue-500/20">

      {/* ==================== NAVIGATION BAR ==================== */}

      <header className="h-[72px] bg-white border-b border-slate-200 flex items-center px-8 sticky top-0 z-20">

        {/* Left - Platform Name */}
        <div className="flex items-center shrink-0">
          <h1 className="text-[22px] font-semibold text-slate-900 tracking-tight">
            ML Training Platform
          </h1>
        </div>


        {/* Right Side - Navigation + Gateway */}
        <div className="ml-auto flex items-center gap-10">

          {/* Navigation */}
          <nav className="flex items-center gap-10">

            <NavItem
              to="/"
              label="Dashboard"
              active={currentPath === '/'}
            />

            <NavItem
              to="/train"
              label="Train"
              active={currentPath === '/train'}
            />

            <NavItem
              to="/workers"
              label="Workers"
              active={currentPath === '/workers'}
            />

            <NavItem
              to="/resources"
              label="Resource Requests"
              active={currentPath === '/resources'}
            />

          </nav>


          {/* Gateway Status */}
          <div
            className={`flex items-center gap-2 text-sm font-medium px-5 py-2.5 border rounded-xl ${healthStatus?.status === 'ok'
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
              : 'bg-rose-50 text-rose-700 border-rose-200'
              }`}
          >

            <div
              className={`w-2 h-2 rounded-full ${healthStatus?.status === 'ok'
                ? 'bg-emerald-500 animate-pulse'
                : 'bg-rose-500'
                }`}
            />

            Gateway:{' '}

            {healthStatus?.status === 'ok'
              ? 'Online'
              : 'Offline'}

          </div>

        </div>

      </header>


      {/* ==================== MAIN CONTENT ==================== */}

      <main className="flex-1 overflow-auto bg-slate-50">

        <div
          className={`w-full mx-auto px-8 py-8 ${currentPath === '/train' ||
            currentPath === '/workers' ||
            currentPath === '/resources'
            ? 'max-w-4xl'
            : 'max-w-7xl'
            }`}
        >

          <div className="mb-6">
            <h2 className="text-2xl font-semibold tracking-tight text-slate-800">
              {pageTitle}
            </h2>
          </div>


          <Routes>

            {/* ==================== DASHBOARD ==================== */}

            <Route
              path="/"
              element={<Dashboard />}
            />


            {/* ==================== TRAIN ==================== */}

            <Route
              path="/train"
              element={<TrainPage />}
            />


            {/* ==================== WORKERS ==================== */}

            <Route
              path="/workers"
              element={<WorkersPage />}
            />


            {/* ==================== RESOURCE REQUESTS ==================== */}

            <Route
              path="/resources"
              element={<ResourceRequestsPage />}
            />

          </Routes>

        </div>

      </main>

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


/* ==================== NAVIGATION ITEM ==================== */

function NavItem({
  label,
  to,
  active,
}: {
  label: string;
  to: string;
  active?: boolean;
}) {
  return (
    <Link
      to={to}
      className={`px-2 py-2 text-[15px] font-medium transition-colors duration-200 ${active
        ? 'text-blue-600'
        : 'text-slate-600 hover:text-slate-900'
        }`}
    >
      {label}
    </Link>
  );
}


/* ==================== STAT CARD ==================== */

function StatCard({
  title,
  value,
}: {
  title: string;
  value: string;
}) {
  return (
    <div className="bg-white border border-slate-200 p-6 rounded-xl shadow-sm hover:shadow-md hover:border-blue-300 transition-all duration-200">

      <h3 className="text-slate-500 text-sm font-medium mb-3">
        {title}
      </h3>

      <div className="text-3xl font-bold text-slate-900 tracking-tight">
        {value}
      </div>

    </div>
  );
}