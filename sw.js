
importScripts('https://www.gstatic.com/firebasejs/12.1.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/12.1.0/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyD0zPaADARoJLxTyDf6GIy_2BOzKc3v8x8",
  authDomain: "compras-ca22d.firebaseapp.com",
  databaseURL: "https://compras-ca22d-default-rtdb.firebaseio.com",
  projectId: "compras-ca22d",
  storageBucket: "compras-ca22d.firebasestorage.app",
  messagingSenderId: "823577033462",
  appId: "1:823577033462:web:1e7794ab17296067f40ad7"
});

const messaging = firebase.messaging();

// Notificações em background (app fechado)
messaging.onBackgroundMessage((payload) => {
  const title = payload.notification?.title || 'Lista de Compras';
  const body  = payload.notification?.body  || 'A lista foi atualizada!';
  self.registration.showNotification(title, {
    body,
    icon: './Carrinho.png',
    badge: './Carrinho.png'
  });
});

// Ao clicar na notificação, abre o app
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('./'));
});

// ── Cache ──────────────────────────────────────────────────────
const CACHE_NAME = 'lista-compras-cache-v10';
const URLS_TO_CACHE = ['./Carrinho.png', './manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(URLS_TO_CACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  // Não cachear Firebase/Google
  if (req.url.includes('firebase') || req.url.includes('googleapis') || req.url.includes('gstatic')) return;
  // Network-first para HTML e JSON (sempre busca versão nova)
  const url = req.url;
  if (url.endsWith('.html') || url.includes('index') || url.includes('.json') || url === location.origin + '/') {
    event.respondWith(
      fetch(req).catch(() => caches.match(req))
    );
    return;
  }
  // Cache-first para imagens e assets estáticos
  event.respondWith(
    caches.match(req).then((cached) => {
      return cached || fetch(req).then((res) => {
        const resClone = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, resClone));
        return res;
      });
    })
  );
});
