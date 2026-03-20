import React, { createContext, useContext, useEffect, useState } from 'react';

interface RuntimeStatus {
  last_action?: string;
  [key: string]: any;
}

interface StatusContextType {
  status: RuntimeStatus;
  health: boolean;
  refreshStatus: () => Promise<void>;
  control: (action: string, payload?: any) => Promise<any>;
}

const StatusContext = createContext<StatusContextType | undefined>(undefined);

export const useStatus = () => {
  const context = useContext(StatusContext);
  if (!context) {
    throw new Error('useStatus must be used within a StatusProvider');
  }
  return context;
};

export const StatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [status, setStatus] = useState<RuntimeStatus>({});
  const [health, setHealth] = useState(false);

  const refreshStatus = async () => {
    try {
      const res = await fetch('/status');
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
        setHealth(true);
      } else {
        setHealth(false);
      }
    } catch (e) {
      setHealth(false);
      console.error('Failed to fetch status', e);
    }
  };

  const control = async (action: string, payload: any = {}) => {
    try {
      const res = await fetch('/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...payload }),
      });
      if (res.ok) {
        await refreshStatus();
        return await res.json();
      }
    } catch (e) {
      console.error('Control action failed', e);
      throw e;
    }
  };

  useEffect(() => {
    void refreshStatus();

    const poll = () => {
      void refreshStatus();
    };

    const interval = window.setInterval(poll, 5000);
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        poll();
      }
    };
    const handleFocus = () => poll();

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleFocus);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
    };
  }, []);

  return (
    <StatusContext.Provider value={{ status, health, refreshStatus, control }}>
      {children}
    </StatusContext.Provider>
  );
};
