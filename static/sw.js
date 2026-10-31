// Yo Delivery service worker: makes the shop installable as a phone app and shows order notifications.
// It does not cache account pages (order and address data must not stay on the phone).

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = { title: 'Yo Delivery', body: '', url: '/account/notifications' };
  try { data = Object.assign(data, event.data ? event.data.json() : {}); } catch (e) { data.body = event.data ? event.data.text() : ''; }
  event.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    icon: '/static/images/app-icon-192.png',
    badge: '/static/images/favicon.png',
    data: { url: data.url },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/account/notifications';
  event.waitUntil(self.clients.matchAll({ type: 'window' }).then((wins) => {
    for (const w of wins) { if ('focus' in w) { w.navigate(url); return w.focus(); } }
    return self.clients.openWindow(url);
  }));
});
