<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		echoCanvasFrames,
		echoCanvasActiveFrame,
		hydrateEchoCanvasFrames,
		removeEchoCanvasFrames,
		type EchoCanvasFrame
	} from '$lib/stores/echo';
	import {
		getEchoToken,
		getEchoWmChatSnapshot,
		postEchoWmRpc
	} from '$lib/apis/echo';
	import { ECHO_IFRAME_SANDBOX } from '$lib/constants';

	export let chatId: string = '';

	const TERMINAL_STATUSES = new Set(['completed', 'killed', 'error', 'timeout']);

	let activeFrameId = '';
	let iframeRefs: Record<string, HTMLIFrameElement | null> = {};
	let srcdocCache: Record<string, { html: string; runId: string; srcdoc: string }> = {};
	let stoppingIds: Record<string, boolean> = {};
	let themeObserver: MutationObserver | null = null;
	let lastHydratedChatId = '';

	$: frames = ($echoCanvasFrames[chatId] || []) as EchoCanvasFrame[];
	$: requestedFrameId = $echoCanvasActiveFrame[chatId] || '';
	$: if (requestedFrameId && frames.some((f) => f.id === requestedFrameId)) {
		activeFrameId = requestedFrameId;
	} else if (frames.length > 0 && (!activeFrameId || !frames.some((f) => f.id === activeFrameId))) {
		activeFrameId = frames[frames.length - 1].id;
	}

	$: activeFrame = frames.find((f) => f.id === activeFrameId) || frames[0] || null;
	$: activeIsTerminal = activeFrame ? TERMINAL_STATUSES.has(activeFrame.server_status || '') : false;

	$: if (chatId && chatId !== lastHydratedChatId) {
		lastHydratedChatId = chatId;
		srcdocCache = {};
		void rehydrateCanvasFrames(chatId);
	}

	const buildSandboxBridgeScript = (): string => `<script>
(function() {
	function makeMemStorage() {
		var store = Object.create(null);
		var api = {
			getItem: function(k) { return Object.prototype.hasOwnProperty.call(store, String(k)) ? store[String(k)] : null; },
			setItem: function(k, v) { store[String(k)] = String(v); },
			removeItem: function(k) { delete store[String(k)]; },
			clear: function() { store = Object.create(null); },
			key: function(i) { var keys = Object.keys(store); return keys[i] !== undefined ? keys[i] : null; }
		};
		return new Proxy(api, {
			get: function(t, p) {
				if (p === 'length') return Object.keys(store).length;
				if (p in t) return t[p];
				return Object.prototype.hasOwnProperty.call(store, String(p)) ? store[String(p)] : undefined;
			},
			set: function(t, p, v) { if (p in t || p === 'length') return false; store[String(p)] = String(v); return true; },
			deleteProperty: function(t, p) { delete store[String(p)]; return true; }
		});
	}
	try { window.localStorage.setItem('__e', '1'); window.localStorage.removeItem('__e'); }
	catch (_) {
		try { Object.defineProperty(window, 'localStorage', { value: makeMemStorage(), configurable: true }); } catch (e) {}
		try { Object.defineProperty(window, 'sessionStorage', { value: makeMemStorage(), configurable: true }); } catch (e) {}
	}
	window.addEventListener('message', function(ev) {
		if (!ev || !ev.data || ev.data.type !== 'theme-update') return;
		if (ev.data.bgColor) document.documentElement.style.setProperty('--echo-bg', ev.data.bgColor);
		if (ev.data.textColor) document.documentElement.style.setProperty('--echo-text', ev.data.textColor);
	});
})();
<\/script>`;

	const injectSandboxBridgeScript = (html: string): string => {
		const bridge = buildSandboxBridgeScript();
		const source = html || '';
		const headMatch = source.match(/<head(?:\s[^>]*)?>/i);
		if (headMatch && headMatch.index !== undefined) {
			const pos = headMatch.index + headMatch[0].length;
			return source.slice(0, pos) + bridge + source.slice(pos);
		}
		const htmlMatch = source.match(/<html(?:\s[^>]*)?>/i);
		if (htmlMatch && htmlMatch.index !== undefined) {
			const pos = htmlMatch.index + htmlMatch[0].length;
			return source.slice(0, pos) + bridge + source.slice(pos);
		}
		const doctypeMatch = source.match(/<!doctype[^>]*>/i);
		if (doctypeMatch && doctypeMatch.index !== undefined) {
			const pos = doctypeMatch.index + doctypeMatch[0].length;
			return source.slice(0, pos) + bridge + source.slice(pos);
		}
		return bridge + source;
	};

	const getFrameSrcdoc = (f: EchoCanvasFrame): string | undefined => {
		if (f.src) return undefined;
		const rawHtml = f.html || '';
		const runId = f.run_id || '';
		const cached = srcdocCache[f.id];
		if (cached && cached.html === rawHtml && cached.runId === runId) {
			return cached.srcdoc;
		}
		const srcdoc = injectSandboxBridgeScript(rawHtml);
		srcdocCache[f.id] = { html: rawHtml, runId, srcdoc };
		return srcdoc;
	};

	const rehydrateCanvasFrames = async (targetChatId: string) => {
		const token = getEchoToken();
		if (!token || !targetChatId) return;
		try {
			const res = await getEchoWmChatSnapshot(token, targetChatId);
			if (res && Array.isArray(res.canvas_frames) && res.canvas_frames.length > 0) {
				hydrateEchoCanvasFrames(targetChatId, res.canvas_frames);
			}
		} catch {
			// Ignorer les erreurs réseau lors de la réhydratation F5
		}
	};

	const broadcastThemeToFrame = (wid: string) => {
		const el = iframeRefs[wid];
		if (!el || !el.contentWindow) return;
		const isDark = document.documentElement.classList.contains('dark');
		el.contentWindow.postMessage(
			{
				type: 'theme-update',
				dark: isDark,
				bgColor: isDark ? '#111827' : '#ffffff',
				textColor: isDark ? '#f3f4f6' : '#1f2937'
			},
			'*'
		);
	};

	const broadcastTheme = () => {
		for (const wid of Object.keys(iframeRefs)) {
			broadcastThemeToFrame(wid);
		}
	};

	const stopFrameServer = async (frame: EchoCanvasFrame) => {
		if (!frame || !chatId || stoppingIds[frame.id]) return;
		const token = getEchoToken();
		if (!token) return;
		stoppingIds = { ...stoppingIds, [frame.id]: true };
		try {
			await postEchoWmRpc(token, {
				action: 'canvas_stop_run',
				chat_id: chatId,
				frame_id: frame.id,
				run_id: frame.run_id || ''
			});
			echoCanvasFrames.update((all) => {
				const list = all[chatId] || [];
				if (list.length === 0) return all;
				return {
					...all,
					[chatId]: list.map((f) =>
						f.id === frame.id || (frame.run_id && f.run_id === frame.run_id)
							? { ...f, server_status: 'killed' }
							: f
					)
				};
			});
		} catch (err: any) {
			toast.error(err?.message || "Échec de l'arrêt du serveur");
		} finally {
			const next = { ...stoppingIds };
			delete next[frame.id];
			stoppingIds = next;
		}
	};

	const closeFrame = (frameId: string) => {
		if (!frameId) return;
		const token = getEchoToken();
		if (token && chatId) {
			void postEchoWmRpc(
				token,
				{
					action: 'canvas_ui_event',
					chat_id: chatId,
					window_closed: [frameId],
					remove_frame: true
				},
				true
			);
		}
		if (chatId) {
			removeEchoCanvasFrames(chatId, [frameId]);
		}
		delete iframeRefs[frameId];
		delete srcdocCache[frameId];
	};

	onMount(() => {
		if (chatId) {
			lastHydratedChatId = chatId;
			void rehydrateCanvasFrames(chatId);
		}
		themeObserver = new MutationObserver(() => {
			broadcastTheme();
		});
		themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
	});

	onDestroy(() => {
		if (themeObserver) {
			themeObserver.disconnect();
			themeObserver = null;
		}
	});
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre supérieure des frames Canvas -->
	<div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0">
		<div class="flex items-center gap-2 overflow-x-auto">
			<span class="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-1.5 flex-shrink-0">
				<svg class="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
					<line x1="8" y1="21" x2="16" y2="21" />
					<line x1="12" y1="17" x2="12" y2="21" />
				</svg>
				Applications
			</span>

			{#if frames.length > 1}
				<div class="flex items-center gap-1 bg-gray-200 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium ml-2">
					{#each frames as f}
						<button
							type="button"
							class="px-2 py-0.5 rounded transition text-xs font-mono {activeFrameId === f.id ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white font-semibold' : 'text-gray-600 dark:text-gray-400'}"
							on:click={() => {
								activeFrameId = f.id;
								if (chatId) {
									echoCanvasActiveFrame.update((curr) => ({ ...curr, [chatId]: f.id }));
								}
							}}
						>
							{f.title || f.id.slice(0, 8)}
						</button>
					{/each}
				</div>
			{/if}
		</div>

		{#if activeFrame}
			<div class="flex items-center gap-2 min-w-0 ml-auto">
				<span
					class="inline-flex shrink-0 items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium {activeIsTerminal ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'}"
				>
					<span class="w-1.5 h-1.5 rounded-full {activeIsTerminal ? 'bg-amber-500' : 'bg-emerald-500'}"></span>
					{activeIsTerminal ? 'Arrêté' : 'Actif'}
				</span>
				<span
					class="text-[11px] font-mono text-gray-400 truncate max-w-[150px] sm:max-w-[260px]"
					title={activeFrame.title || activeFrame.id}
				>
					{activeFrame.title || activeFrame.id}
				</span>
				{#if !activeIsTerminal && Boolean(activeFrame.run_id)}
					<button
						type="button"
						disabled={Boolean(stoppingIds[activeFrame.id])}
						class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/50 transition"
						title="Arrêter le serveur"
						on:click={() => stopFrameServer(activeFrame)}
					>
						<svg class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
							<rect x="6" y="6" width="12" height="12" rx="1" />
						</svg>
						{stoppingIds[activeFrame.id] ? 'Arrêt…' : 'Arrêter'}
					</button>
				{/if}
				<button
					type="button"
					class="p-1 rounded text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
					title="Recharger l'application"
					on:click={() => {
						const el = iframeRefs[activeFrame.id];
						if (el) {
							if (activeFrame.src) el.src = activeFrame.src;
							else {
								const doc = getFrameSrcdoc(activeFrame);
								if (doc !== undefined) el.srcdoc = doc;
							}
						}
					}}
				>
					<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
					</svg>
				</button>
				<button
					type="button"
					class="p-1 rounded text-gray-500 hover:text-red-600 dark:hover:text-red-400"
					title="Fermer l'application"
					on:click={() => closeFrame(activeFrame.id)}
				>
					<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<line x1="18" y1="6" x2="6" y2="18" />
						<line x1="6" y1="6" x2="18" y2="18" />
					</svg>
				</button>
			</div>
		{/if}
	</div>

	<!-- Zone de rendu iframes sandboxées -->
	<div class="flex-1 w-full h-full relative bg-gray-100 dark:bg-gray-950">
		{#if frames.length === 0}
			<div class="flex flex-col items-center justify-center h-full text-gray-400 text-xs">
				<svg class="w-12 h-12 mb-2 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
					<rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
					<line x1="8" y1="21" x2="16" y2="21" />
					<line x1="12" y1="17" x2="12" y2="21" />
				</svg>
				Aucune interface active émise par l'assistant ou le Window Manager.
			</div>
		{:else}
			{#each frames as f (f.id)}
				<iframe
					bind:this={iframeRefs[f.id]}
					title={f.title || 'ECHO Applications'}
					src={f.src || undefined}
					srcdoc={getFrameSrcdoc(f)}
					sandbox={ECHO_IFRAME_SANDBOX}
					class="w-full h-full border-none"
					class:hidden={f.id !== activeFrameId}
					on:load={() => broadcastThemeToFrame(f.id)}
				></iframe>
			{/each}
		{/if}
	</div>
</div>
