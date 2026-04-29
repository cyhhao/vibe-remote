import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';

export type ThemeMode = 'system' | 'light' | 'dark';
type ResolvedTheme = 'light' | 'dark';

type ThemeContextValue = {
  mode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  cycleMode: () => void;
};

const STORAGE_KEY = 'vibe-remote-theme';
const VALID_MODES: ThemeMode[] = ['system', 'light', 'dark'];

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode !== 'system') {
    return mode;
  }

  if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light';
  }

  return 'dark';
}

function applyTheme(mode: ThemeMode) {
  if (typeof document === 'undefined') {
    return;
  }

  document.documentElement.setAttribute('data-theme', resolveTheme(mode));
}

function readStoredTheme(): ThemeMode {
  try {
    const queryMode = new URLSearchParams(window.location.search).get('theme');
    if (queryMode && VALID_MODES.includes(queryMode as ThemeMode)) {
      return queryMode as ThemeMode;
    }

    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value && VALID_MODES.includes(value as ThemeMode)) {
      return value as ThemeMode;
    }
  } catch {
    // Ignore storage issues and fall back to the design's default dark canvas.
  }

  return 'dark';
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [mode, setModeState] = useState<ThemeMode>('dark');
  const resolvedTheme = useMemo(() => resolveTheme(mode), [mode]);

  useEffect(() => {
    const nextMode = readStoredTheme();
    setModeState(nextMode);
    applyTheme(nextMode);

    const mediaQuery = window.matchMedia('(prefers-color-scheme: light)');
    const handleChange = () => {
      if (readStoredTheme() === 'system') {
        applyTheme('system');
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  const setMode = (nextMode: ThemeMode) => {
    setModeState(nextMode);

    try {
      if (nextMode === 'system') {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, nextMode);
      }
    } catch {
      // Ignore storage issues.
    }

    applyTheme(nextMode);
  };

  const cycleMode = () => {
    const nextMode: ThemeMode = mode === 'system' ? 'light' : mode === 'light' ? 'dark' : 'system';
    setMode(nextMode);
  };

  return (
    <ThemeContext.Provider value={{ mode, resolvedTheme, setMode, cycleMode }}>
      {children}
    </ThemeContext.Provider>
  );
};

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return context;
}
