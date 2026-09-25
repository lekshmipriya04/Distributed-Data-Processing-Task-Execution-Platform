import { createContext, useContext, useState, ReactNode, useEffect } from 'react';
import type { ClusterConfig } from '../types';

const STORAGE_KEY = 'platform_cluster_config';

const DEFAULT_CONFIG: ClusterConfig = {
  mode: 'single',
  localCores: 4,
  nodes: [],
};

function loadInitial(): ClusterConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    /* ignore corrupt storage */
  }
  return DEFAULT_CONFIG;
}

interface ClusterConfigContextValue {
  config: ClusterConfig;
  setConfig: (config: ClusterConfig) => void;
}

const ClusterConfigContext = createContext<ClusterConfigContextValue | undefined>(undefined);

export function ClusterConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfigState] = useState<ClusterConfig>(loadInitial);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  }, [config]);

  return (
    <ClusterConfigContext.Provider value={{ config, setConfig: setConfigState }}>
      {children}
    </ClusterConfigContext.Provider>
  );
}

export function useClusterConfig() {
  const ctx = useContext(ClusterConfigContext);
  if (!ctx) throw new Error('useClusterConfig must be used within ClusterConfigProvider');
  return ctx;
}
