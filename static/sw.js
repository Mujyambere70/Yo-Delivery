// Yo Delivery service worker: makes the shop installable as a phone app and shows order notifications.
// It does not cache account pages (order and address data must not stay on the phone).
// Version 6: offline mode for couriers - the pages of the courier app (/driver...) are kept for 12 hours so the
// courier can still read the address and the jobs without network; the copy is deleted when he signs out.

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

const COURIER_CACHE = 'yo-courier-v6';
const KEEP_MS = 12 * 60 * 60 * 1000;
const isCourierPage = (url) => url.origin === self.location.origin && (url.pathname === '/driver' || url.pathname.startsWith('/driver/job/') || url.pathname === '/driver/route');
const isAsset = (url) => url.origin === self.location.origin && url.pathname.startsWith('/static/');

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.pathname === '/logout') {                 // signing out removes the offline copy
    event.waitUntil(caches.delete(COURIER_CACHE));
    return;
  }
  if (!isCourierPage(url) && !(isAsset(url) && req.referrer && new URL(req.referrer).pathname.startsWith('/driver'))) return;
  event.respondWith(fetch(req).then((resp) => {      // network first; the copy is only for when there is no network
    if (resp.ok && resp.type === 'basic') {
      const copy = resp.clone();
      caches.open(COURIER_CACHE).then((c) => c.put(req, copy)).catch(() => {});
      caches.open(COURIER_CACHE).then((c) => c.put(new Request(req.url + (req.url.includes('?') ? '&' : '?') + '__t'), new Response(String(Date.now())))).catch(() => {});
    }
    return resp;
  }).catch(() => caches.open(COURIER_CACHE).then(async (c) => {
    const t = await c.match(req.url + (req.url.includes('?') ? '&' : '?') + '__t');
    const age = t ? Date.now() - Number(await t.text()) : 0;
    const hit = await c.match(req);
    if (hit && age < KEEP_MS) return hit;
    return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Offline</title>' +
      '<body style="font-family:sans-serif;padding:24px"><h1>No network</h1><p>This page was not saved on your phone. Open the courier app again when you have network.</p>',
      { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 });
  })));
});
