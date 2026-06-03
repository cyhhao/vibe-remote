import { useCallback, useEffect, useState } from 'react';

import { useApi } from '../context/ApiContext';

// Shared remote-auth account state: the signed-in email + a sign-out action.
// Consumed by the desktop AccountMenu dropdown and the mobile More page so the
// auth fetch + sign-out flow lives in one place. email is null when this isn't a
// remote/authenticated session (e.g. local setups) — callers hide the account UI.
export function useAuthAccount() {
  const { getAuthSession, signOut } = useApi();
  const [email, setEmail] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAuthSession()
      .then((session) => {
        if (cancelled) return;
        setEmail(session.remote && session.authenticated ? session.email : null);
      })
      .catch(() => {
        if (!cancelled) setEmail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [getAuthSession]);

  const handleSignOut = useCallback(async () => {
    if (signingOut) return;
    setSigningOut(true);
    // Always navigate to "/" even on error: the cookie may already be expired
    // (the request would 401), so leaving the user stuck would be worse — let
    // the OIDC redirect handle whatever state remains.
    try {
      await signOut();
    } catch {
      // swallow — we still reload below
    }
    window.location.assign('/');
  }, [signingOut, signOut]);

  return { email, signingOut, signOut: handleSignOut };
}
