
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
const CACHE_NAME = 'lista-compras-cache-v15';
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
  // Network-first para a página e os catálogos (sempre a versão nova).
  //
  // A checagem anterior era por URL e não cobria o endereço do GitHub
  // Pages (.../Lista-de-Compras-Dev/): não termina em .html, não contém
  // "index" nem ".json", e não é igual a origin + "/". Com isso a página
  // caía no cache-first abaixo e o app ficava preso numa versão antiga
  // do index.html, mesmo recebendo preços novos. Usar req.mode cobre a
  // navegação em qualquer caminho.
  if (req.mode === 'navigate' || req.destination === 'document') {
    event.respondWith(
      fetch(req).catch(() => caches.match(req).then((c) => c || caches.match('./')))
    );
    return;
  }

  // Catálogos: vai à rede, mas guarda uma cópia. Com a URL estável (sem
  // carimbo de tempo), o GitHub Pages responde 304 sem corpo quando nada
  // mudou, então "ir à rede" custa alguns bytes em vez de 6,6 MB. A cópia
  // guardada é o que faz o app abrir sem internet.
  if (req.url.includes('.json')) {
    event.respondWith(
      // 'no-cache' aqui não quer dizer "não use cache": quer dizer
      // "pergunte sempre se mudou". O navegador manda o ETag, o servidor
      // responde 304 sem corpo quando nada mudou, e o próprio navegador
      // devolve a cópia guardada. Sem isso ele decide sozinho não
      // perguntar por um tempo, e o app fica com preço velho depois da
      // atualização semanal.
      fetch(req, { cache: 'no-cache' })
        .then((res) => {
          if (res.ok) {
            const copia = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copia));
          }
          return res;
        })
        .catch(() => caches.match(req))
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
