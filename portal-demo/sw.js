/* Service Worker do AstroDicas — cache mínimo para instalação + push */

const CACHE_NAME = 'astrodicas-shell-v1';

// Apenas o shell estático — leituras são dinâmicas e nunca vão para cache.
const SHELL_URLS = [
  '/',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
  '/assets/apple-touch-icon.png',
  '/assets/favicon-32.png',
];

// ---------------------------------------------------------------------------
// Install: pré-cache do shell
// ---------------------------------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate: limpa caches antigos
// ---------------------------------------------------------------------------
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch: network-first para tudo (segurança: nunca serve leitura de outro usuário)
// Apenas o shell vira fallback de cache quando offline.
// ---------------------------------------------------------------------------
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Requisições de API e leituras: sempre network, sem cache
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        // Atualiza cache do shell em segundo plano
        if (event.request.method === 'GET' && resp.ok && SHELL_URLS.includes(url.pathname)) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
        }
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});

// ---------------------------------------------------------------------------
// Push: recebe notificação do servidor
// ---------------------------------------------------------------------------
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}

  const title = data.title || 'AstroDicas';
  const body  = data.body  || '';
  const tag   = data.type  || 'astrodicas';
  const icon  = '/assets/icon-192.png';
  const badge = '/assets/favicon-32.png';

  // Verifica se há aba aberta e visível — se houver, avisa via BroadcastChannel
  // em vez de mostrar notificação do sistema.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const visible = clients.filter((c) => c.visibilityState === 'visible');
      if (visible.length > 0) {
        // Aba aberta: dispara in-app toast em vez de notificação de sistema
        const channel = new BroadcastChannel('astrodicas-push');
        channel.postMessage({ type: data.type || 'push', title, body, data });
        channel.close();
        return;
      }
      // Nenhuma aba visível: mostra notificação do sistema
      return self.registration.showNotification(title, {
        body,
        icon,
        badge,
        tag,
        data,
        renotify: false,
      });
    })
  );
});

// ---------------------------------------------------------------------------
// Notification click: foca aba existente ou abre nova
// ---------------------------------------------------------------------------
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow('/');
    })
  );
});
