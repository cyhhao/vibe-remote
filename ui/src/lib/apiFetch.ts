const CSRF_COOKIE_NAME = 'vibe_csrf_token';
const CSRF_HEADER_NAME = 'X-Vibe-CSRF-Token';
const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

let csrfTokenPromise: Promise<string> | null = null;

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') {
    return null;
  }

  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

async function fetchCsrfToken(): Promise<string> {
  const response = await fetch('/api/csrf-token', {
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch CSRF token (${response.status})`);
  }
  const payload = await response.json();
  const token = typeof payload?.csrf_token === 'string' ? payload.csrf_token : '';
  if (!token) {
    throw new Error('Missing CSRF token in response');
  }
  return token;
}

export async function ensureCsrfToken(): Promise<string> {
  const existing = readCookie(CSRF_COOKIE_NAME);
  if (existing) {
    return existing;
  }

  if (!csrfTokenPromise) {
    csrfTokenPromise = fetchCsrfToken().finally(() => {
      csrfTokenPromise = null;
    });
  }
  return csrfTokenPromise;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || 'GET').toUpperCase();
  const nextInit: RequestInit = { ...init };

  if (MUTATING_METHODS.has(method)) {
    const token = await ensureCsrfToken();
    const headers = new Headers(init.headers || {});
    headers.set(CSRF_HEADER_NAME, token);
    nextInit.headers = headers;
  }

  return fetch(input, nextInit);
}
