const C='ss-v1',F=['./','icons/icon-180.png'];
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(c=>c.addAll(F))));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET'||new URL(e.request.url).pathname.startsWith('/api/'))return;
e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
