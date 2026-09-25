import { createContext, useContext, useState, ReactNode } from 'react';
import { getToken, setToken as persistToken, clearToken } from '../api/client';

interface AuthContextValue {
  token: string | null;
  setToken: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(getToken());

  const setToken = (t: string) => {
    persistToken(t);
    setTokenState(t);
  };

  const logout = () => {
    clearToken();
    setTokenState(null);
  };

  return <AuthContext.Provider value={{ token, setToken, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
