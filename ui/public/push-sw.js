self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }

  const title = typeof payload.title === 'string' && payload.title ? payload.title : 'avibe';
  const url = typeof payload.url === 'string' && payload.url ? payload.url : '/inbox';
  const options = {
    body: typeof payload.body === 'string' ? payload.body : '',
    tag: typeof payload.tag === 'string' ? payload.tag : undefined,
    renotify: typeof payload.tag === 'string' && payload.tag.length > 0,
    data: { url },
    icon: '/icon-192.png',
    badge: '/icon-192.png',
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data?.url || '/inbox', self.location.origin);
  if (targetUrl.origin !== self.location.origin) {
    targetUrl.href = new URL('/inbox', self.location.origin).href;
  }
  const href = targetUrl.href;
  const message = {
    type: 'vibe.notification-click',
    url: targetUrl.pathname + targetUrl.search + targetUrl.hash,
  };
  const appShellPaths = ['/inbox', '/agents', '/skills', '/harness', '/vaults', '/projects', '/more', '/chat', '/admin'];
  const isAppShellClient = (url) => {
    if (url.origin !== self.location.origin) return false;
    if (url.pathname === '/') return true;
    return appShellPaths.some((path) => url.pathname === path || url.pathname.startsWith(`${path}/`));
  };

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client && isAppShellClient(new URL(client.url))) {
          return client.focus().then((focusedClient) => {
            (focusedClient || client).postMessage(message);
            return focusedClient || client;
          });
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(href);
      }
      return undefined;
    }),
  );
});
