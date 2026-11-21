<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		echoCanvasFrames,
		echoCanvasActiveFrame,
		echoCanvasEventQueues,
		drainEchoCanvasEvents,
		hydrateEchoCanvasFrames,
		removeEchoCanvasFrames,
		type EchoCanvasFrame
	} from '$lib/stores/echo';
	import { getEchoToken, getEchoWmChatSnapshot, postEchoWmRpc } from '$lib/apis/echo';

	export let chatId: string = '';

	const HEARTBEAT_INTERVAL_MS = 5000;
	const TERMINAL_STATUSES = new Set(['completed', 'killed', 'error', 'timeout']);

	let activeFrameId = '';
	let iframeRefs: Record<string, HTMLIFrameElement | null> = {};
	let iframeReady: Record<string, boolean> = {};
	let srcdocCache: Record<string, { html: string; runId: string; srcdoc: string }> = {};
	let unsubscribeQueues: (() => void) | null = null;
	let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
	let themeObserver: MutationObserver | null = null;
	let lastHydratedChatId = '';
	let lastRunningKey = '';

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
		void rehydrateCanvasFrames(chatId);
	}

	$: {
		const runningIds = frames
			.filter((f) => !TERMINAL_STATUSES.has(f.server_status || ''))
			.map((f) => f.id);
		const runningKey = `${chatId}::${runningIds.join(',')}`;
		if (chatId && runningIds.length > 0 && runningKey !== lastRunningKey) {
			lastRunningKey = runningKey;
			void sendHostHeartbeat();
		} else if (runningIds.length === 0) {
			lastRunningKey = '';
		}
	}

	const buildSandboxBridgeScript = (windowId: string, runId: string = ''): string => {
		const widJson = JSON.stringify(windowId || 'default');
		const runIdJson = JSON.stringify(runId || '');
		return `<script>
(function() {
	var WID = ${widJson};
	var RUN_ID = ${runIdJson};
	try { window.parent.postMessage({ type: 'echo:iframe:init', window_id: WID }, '*'); } catch (e) {}
	window._echoPendingReqs = {};
	window._echoEventListeners = [];
	window._echoStatusListeners = [];
	window._echoServerStatus = null;
	window.addEventListener('message', function(ev) {
		if (ev.data && ev.data.type === 'theme-update') {
			if (ev.data.bgColor) document.documentElement.style.setProperty('--echo-bg', ev.data.bgColor);
			if (ev.data.textColor) document.documentElement.style.setProperty('--echo-text', ev.data.textColor);
		}
		if (ev.data && ev.data.echoSandboxEvent !== undefined) {
			var rid = ev.data.req_id;
			if (rid && window._echoPendingReqs[rid]) {
				var entry = window._echoPendingReqs[rid];
				clearTimeout(entry.timer);
				delete window._echoPendingReqs[rid];
				entry.resolve(ev.data.echoSandboxEvent);
			}
			for (var i = 0; i < window._echoEventListeners.length; i++) {
				try { window._echoEventListeners[i](ev.data.echoSandboxEvent, rid); } catch (e) {}
			}
		}
		if (ev.data && ev.data.echoSandboxStatus !== undefined) {
			window._echoServerStatus = ev.data.echoSandboxStatus;
			var st = ev.data.echoSandboxStatus && ev.data.echoSandboxStatus.server_status;
			for (var j = 0; j < window._echoStatusListeners.length; j++) {
				try { window._echoStatusListeners[j](ev.data.echoSandboxStatus); } catch (e) {}
			}
			if (st === 'completed' || st === 'killed' || st === 'error' || st === 'timeout') {
				var keys = Object.keys(window._echoPendingReqs);
				for (var k = 0; k < keys.length; k++) {
					var pending = window._echoPendingReqs[keys[k]];
					if (pending) {
						clearTimeout(pending.timer);
						delete window._echoPendingReqs[keys[k]];
						try { pending.reject(new Error('Sandbox stopped (' + st + ')')); } catch (e) {}
					}
				}
			}
		}
	});
	window.echoRequest = function(action, payload, timeoutMs) {
		var reqId = 'req_' + Math.random().toString(36).slice(2) + '_' + Date.now();
		var ttl = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 30000;
		return new Promise(function(resolve, reject) {
			var timer = setTimeout(function() {
				if (window._echoPendingReqs[reqId]) {
					delete window._echoPendingReqs[reqId];
					reject(new Error('echoRequest timeout (' + ttl + 'ms)'));
				}
			}, ttl);
			window._echoPendingReqs[reqId] = { resolve: resolve, reject: reject, timer: timer };
			window.parent.postMessage({ echoUIRequest: { window_id: WID, run_id: RUN_ID, action: action, payload: payload, req_id: reqId } }, '*');
		});
	};
	window.onEchoEvent = function(cb) {
		if (typeof cb === 'function') window._echoEventListeners.push(cb);
	};
	window.onEchoStatus = function(cb) {
		if (typeof cb === 'function') window._echoStatusListeners.push(cb);
	};
})();
<\/script>`;
	};

	const injectSandboxBridgeScript = (html: string, windowId: string, runId: string = ''): string => {
		const bridge = buildSandboxBridgeScript(windowId, runId);
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
		const srcdoc = injectSandboxBridgeScript(rawHtml, f.id, runId);
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

	const sendHostHeartbeat = async () => {
		if (!chatId) return;
		const runningIds = frames
			.filter((f) => !TERMINAL_STATUSES.has(f.server_status || ''))
			.map((f) => f.id);
		if (runningIds.length === 0) return;
		const token = getEchoToken();
		if (!token) return;
		try {
			await postEchoWmRpc(token, {
				action: 'canvas_ui_event',
				chat_id: chatId,
				echoPings: runningIds
			});
		} catch {
			// Ignorer les erreurs réseau transitoires de heartbeat
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

	const flushEventsForFrame = (wid: string) => {
		if (!chatId || !iframeReady[wid]) return;
		const el = iframeRefs[wid];
		if (!el || !el.contentWindow) return;
		const events = drainEchoCanvasEvents(chatId, wid);
		for (const ev of events) {
			if (ev.isStatusEvent) {
				el.contentWindow.postMessage(
					{
						echoSandboxStatus: ev.data,
						window_id: wid
					},
					'*'
				);
			} else {
				el.contentWindow.postMessage(
					{
						echoSandboxEvent: ev.data,
						req_id: ev.reqId ?? null,
						window_id: wid
					},
					'*'
				);
			}
		}
	};

	const handleIframeReady = (wid: string) => {
		iframeReady[wid] = true;
		broadcastThemeToFrame(wid);
		flushEventsForFrame(wid);
	};

	const handleMessage = async (e: MessageEvent) => {
		if (!e.data || typeof e.data !== 'object') return;
		const sourceWid = Object.keys(iframeRefs).find(
			(wid) => iframeRefs[wid]?.contentWindow === e.source
		);
		if (!sourceWid) return;

		if (e.data.type === 'echo:iframe:init') {
			handleIframeReady(sourceWid);
			return;
		}

		if (
			e.data.echoUIPayload !== undefined ||
			e.data.echoUIRequest !== undefined ||
			e.data.echoPing !== undefined ||
			e.data.echoSignal === 'abort'
		) {
			const token = getEchoToken();
			if (!token) return;
			const targetFrame = frames.find((f) => f.id === sourceWid);
			const effectiveRunId = targetFrame?.run_id || e.data.echoUIRequest?.run_id || '';
			try {
				await postEchoWmRpc(token, {
					action: 'canvas_ui_event',
					chat_id: chatId,
					frame_id: sourceWid,
					run_id: effectiveRunId,
					echoUIPayload: e.data.echoUIPayload,
					echoUIRequest: e.data.echoUIRequest,
					echoPing: e.data.echoPing,
					echoSignal: e.data.echoSignal
				});
			} catch {
				// Ignorer les erreurs réseau transitoires de télémétrie Canvas
			}
			return;
		}

		// Relais RPC venant de l'application Canvas vers le backend ECHO
		if (e.data.type === 'echo:rpc' || e.data.type === 'wm_rpc') {
			const token = getEchoToken();
			if (!token) return;
			const targetEl = iframeRefs[sourceWid];
			try {
				const response = await postEchoWmRpc(token, {
					chat_id: chatId,
					frame_id: sourceWid,
					...e.data.payload
				});
				if (targetEl && targetEl.contentWindow) {
					targetEl.contentWindow.postMessage(
						{ type: 'echo:rpc_response', request_id: e.data.request_id, response },
						'*'
					);
				}
			} catch (err: any) {
				if (targetEl && targetEl.contentWindow) {
					targetEl.contentWindow.postMessage(
						{ type: 'echo:rpc_error', request_id: e.data.request_id, error: err.message },
						'*'
					);
				}
			}
		}
	};

	const stopFrameServer = async (frame: EchoCanvasFrame) => {
		if (!frame || !chatId) return;
		const token = getEchoToken();
		if (!token) return;
		try {
			await postEchoWmRpc(token, {
				action: 'canvas_stop_run',
				chat_id: chatId,
				frame_id: frame.id,
				run_id: frame.run_id || ''
			});
		} catch {
			// Ignorer les erreurs réseau transitoires lors de l'arrêt du serveur
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
		delete iframeReady[frameId];
		delete srcdocCache[frameId];
	};

	onMount(() => {
		window.addEventListener('message', handleMessage);
		unsubscribeQueues = echoCanvasEventQueues.subscribe(() => {
			for (const wid of Object.keys(iframeReady)) {
				if (iframeReady[wid]) {
					flushEventsForFrame(wid);
				}
			}
		});
		if (chatId) {
			lastHydratedChatId = chatId;
			void rehydrateCanvasFrames(chatId);
		}
		void sendHostHeartbeat();
		heartbeatTimer = setInterval(() => {
			void sendHostHeartbeat();
		}, HEARTBEAT_INTERVAL_MS);
		themeObserver = new MutationObserver(() => {
			broadcastTheme();
		});
		themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('message', handleMessage);
		}
		if (heartbeatTimer) {
			clearInterval(heartbeatTimer);
			heartbeatTimer = null;
		}
		if (unsubscribeQueues) {
			unsubscribeQueues();
			unsubscribeQueues = null;
		}
		if (themeObserver) {
			themeObserver.disconnect();
			themeObserver = null;
		}
	});
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre supérieure des frames Canvas -->
	<div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850">
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
			<div class="flex items-center gap-2">
				<span
					class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium {activeIsTerminal ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'}"
				>
					<span class="w-1.5 h-1.5 rounded-full {activeIsTerminal ? 'bg-amber-500' : 'bg-emerald-500'}"></span>
					{activeIsTerminal ? 'Arrêté' : 'Actif'}
				</span>
				<span class="text-[11px] font-mono text-gray-400">
					{activeFrame.title || activeFrame.id}
				</span>
				{#if !activeIsTerminal && Boolean(activeFrame.run_id)}
					<button
						type="button"
						class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/50 transition"
						title="Arrêter le serveur"
						on:click={() => stopFrameServer(activeFrame)}
					>
						<svg class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
							<rect x="6" y="6" width="12" height="12" rx="1" />
						</svg>
						Arrêter
					</button>
				{/if}
				<button
					type="button"
					class="p-1 rounded text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
					title="Recharger l'application"
					on:click={() => {
						const el = iframeRefs[activeFrame.id];
						if (el) {
							iframeReady[activeFrame.id] = false;
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
					sandbox="allow-scripts allow-downloads allow-forms allow-modals allow-popups"
					class="w-full h-full border-none"
					class:hidden={f.id !== activeFrameId}
					on:load={() => handleIframeReady(f.id)}
				></iframe>
			{/each}
		{/if}
	</div>
</div>
