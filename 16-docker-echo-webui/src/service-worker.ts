/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

import { version } from '$service-worker';

const sw = self as unknown as ServiceWorkerGlobalScope;

const IMMUTABLE_CACHE = `echo-immutable-${version}`;
const STATIC_CACHE = `echo-static-${version}`;
const MAX_STATIC_ENTRIES = 128;

const BYPASS_EXACT_PATHS = new Set([
	'/service-worker.js',
	'/sw.js',
	'/_app/version.json',
	'/sso-logout'
]);

const BYPASS_PREFIXES = ['/api/', '/echo/', '/ws', '/ollama/', '/openai/', '/oauth/', '/auth/'];

function shouldBypass(request: Request, url: URL): boolean {
	if (request.method !== 'GET') return true;
	if (url.origin !== sw.location.origin) return true;
	if (request.headers.has('range')) return true;
	if (request.mode === 'navigate') return true;

	const accept = request.headers.get('accept') || '';
	if (accept.includes('text/html')) return true;

	const { pathname } = url;
	if (BYPASS_EXACT_PATHS.has(pathname)) return true;
	if (BYPASS_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return true;
	if (pathname.endsWith('.mp4') || pathname.endsWith('.webm')) return true;

	return false;
}

function isStaticAsset(pathname: string): boolean {
	if (
		pathname.startsWith('/wasm/') ||
		pathname.startsWith('/pyodide/') ||
		pathname.startsWith('/audio/')
	) {
		return true;
	}
	return pathname.endsWith('.woff2') || pathname.endsWith('.woff') || pathname.endsWith('.ttf');
}

async function trimCache(cache: Cache, maxEntries: number): Promise<void> {
	const keys = await cache.keys();
	while (keys.length > maxEntries) {
		const oldest = keys.shift();
		if (oldest) {
			await cache.delete(oldest);
		}
	}
}

async function cacheFirst(
	request: Request,
	cacheName: string,
	maxEntries?: number
): Promise<Response> {
	const cache = await caches.open(cacheName);
	const cached = await cache.match(request);
	if (cached) return cached;

	const response = await fetch(request);
	if (response.status === 200 && response.type === 'basic') {
		await cache.put(request, response.clone());
		if (maxEntries !== undefined) {
			await trimCache(cache, maxEntries);
		}
	}
	return response;
}

async function purgeAllEchoCaches(): Promise<void> {
	const keys = await caches.keys();
	await Promise.all(keys.filter((key) => key.startsWith('echo-')).map((key) => caches.delete(key)));
}

// Pas de cache.addAll massif ni de skipWaiting() automatique a l'installation
sw.addEventListener('install', () => {});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		(async () => {
			const keys = await caches.keys();
			await Promise.all(
				keys
					.filter((key) => key.startsWith('echo-') && key !== IMMUTABLE_CACHE && key !== STATIC_CACHE)
					.map((key) => caches.delete(key))
			);
			await sw.clients.claim();
		})()
	);
});

sw.addEventListener('message', (event) => {
	const type = event.data?.type;
	if (type === 'SKIP_WAITING') {
		event.waitUntil(sw.skipWaiting());
		return;
	}

	if (type === 'PURGE_CACHES') {
		event.waitUntil(
			(async () => {
				await purgeAllEchoCaches();
				event.ports?.[0]?.postMessage({ ok: true });
			})()
		);
	}
});

sw.addEventListener('fetch', (event) => {
	const url = new URL(event.request.url);
	if (shouldBypass(event.request, url)) {
		return;
	}

	if (url.pathname.startsWith('/_app/immutable/')) {
		event.respondWith(cacheFirst(event.request, IMMUTABLE_CACHE));
		return;
	}

	if (isStaticAsset(url.pathname)) {
		event.respondWith(cacheFirst(event.request, STATIC_CACHE, MAX_STATIC_ENTRIES));
	}
});
