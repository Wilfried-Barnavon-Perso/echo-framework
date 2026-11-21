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
	import {
		getEchoToken,
		getEchoWmChatSnapshot,
		postEchoWmRpc,
		getEchoCanvasWsUrl
	} from '$lib/apis/echo';

	export let chatId: string = '';

	const TERMINAL_STATUSES = new Set(['completed', 'killed', 'error', 'timeout']);

	interface RunSocketEntry {
		ws: WebSocket | null;
		authed: boolean;
		outQueue: Array<string | ArrayBuffer>;
		reconnectTimer: ReturnType<typeof setTimeout> | null;
		closedByClient: boolean;
	}

	let activeFrameId = '';
	let iframeRefs: Record<string, HTMLIFrameElement | null> = {};
	let iframeReady: Record<string, boolean> = {};
	let framePorts: Record<string, MessagePort> = {};
	let pendingPortMessages: Record<string, Array<{ payload: any; transfer?: Transferable[] }>> = {};
	let srcdocCache: Record<string, { html: string; runId: string; srcdoc: string }> = {};
	const runSockets = new Map<string, RunSocketEntry>();
	let unsubscribeQueues: (() => void) | null = null;
	let themeObserver: MutationObserver | null = null;
	let lastHydratedChatId = '';
	let activeSocketChatId = '';

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
		if (activeSocketChatId && activeSocketChatId !== chatId) {
			closeAllRunSockets();
			closeAllFramePorts();
		}
		activeSocketChatId = chatId;
		void rehydrateCanvasFrames(chatId);
	}

	$: if (typeof window !== 'undefined' && chatId) {
		syncRunSockets(chatId, frames);
	}

	const buildSandboxBridgeScript = (windowId: string, runId: string = ''): string => {
		const widJson = JSON.stringify(windowId || 'default');
		const runIdJson = JSON.stringify(runId || '');
		return `<script>
(function() {
	var WID = ${widJson};
	var RUN_ID = ${runIdJson};
	var echoPort = null;
	var echoPortQueue = [];
	window._echoPendingReqs = {};
	window._echoEventListeners = [];
	window._echoStatusListeners = [];
	window._echoServerStatus = null;
	function postToHost(msg, transfer) {
		if (echoPort) {
			if (transfer && transfer.length) echoPort.postMessage(msg, transfer);
			else echoPort.postMessage(msg);
		} else {
			echoPortQueue.push({ msg: msg, transfer: transfer || null });
		}
	}
	function handleHostEvent(ev) {
		if (!ev || !ev.data || typeof ev.data !== 'object') return;
		if (ev.data.type === 'theme-update') {
			if (ev.data.bgColor) document.documentElement.style.setProperty('--echo-bg', ev.data.bgColor);
			if (ev.data.textColor) document.documentElement.style.setProperty('--echo-text', ev.data.textColor);
		}
		if (ev.data.echoSandboxEvent !== undefined) {
			var evtObj = ev.data.echoSandboxEvent;
			var hasEnvelope = evtObj && typeof evtObj === 'object' && ('data' in evtObj);
			var evtData = hasEnvelope ? evtObj.data : evtObj;
			var rid = hasEnvelope && ('req_id' in evtObj) ? evtObj.req_id : ev.data.req_id;
			var bin = hasEnvelope && (evtObj.binary instanceof ArrayBuffer) ? evtObj.binary : null;
			if (rid && window._echoPendingReqs[rid]) {
				var entry = window._echoPendingReqs[rid];
				clearTimeout(entry.timer);
				delete window._echoPendingReqs[rid];
				var resVal = evtData;
				if (bin !== null) {
					if (resVal && typeof resVal === 'object') {
						resVal.binary = bin;
					} else if (resVal === null || resVal === undefined) {
						resVal = bin;
					} else {
						resVal = { value: resVal, binary: bin };
					}
				}
				entry.resolve(resVal);
			}
			for (var i = 0; i < window._echoEventListeners.length; i++) {
				try { window._echoEventListeners[i](evtData, rid, bin); } catch (e) {}
			}
		}
		if (ev.data.echoSandboxStatus !== undefined) {
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
	}
	window.addEventListener('message', function(ev) {
		if (ev.data && ev.data.type === 'echo:port:attach' && ev.ports && ev.ports[0]) {
			echoPort = ev.ports[0];
			echoPort.onmessage = handleHostEvent;
			if (typeof echoPort.start === 'function') echoPort.start();
			while (echoPortQueue.length > 0) {
				var item = echoPortQueue.shift();
				if (item.transfer && item.transfer.length) echoPort.postMessage(item.msg, item.transfer);
				else echoPort.postMessage(item.msg);
			}
			return;
		}
		handleHostEvent(ev);
	});
	try { window.parent.postMessage({ type: 'echo:iframe:init', window_id: WID }, '*'); } catch (e) {}
	window.echoRequest = function(action, payload, binaryOrTimeout, maybeTimeoutMs) {
		var reqId = 'req_' + Math.random().toString(36).slice(2) + '_' + Date.now();
		var binArg = null;
		var timeoutMs = maybeTimeoutMs;
		if (typeof binaryOrTimeout === 'number') {
			timeoutMs = binaryOrTimeout;
		} else if (binaryOrTimeout !== undefined && binaryOrTimeout !== null) {
			binArg = binaryOrTimeout;
		}
		var ttl = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 30000;
		var transferBuf = null;
		var transferList = [];
		if (binArg instanceof ArrayBuffer) {
			transferBuf = binArg;
			transferList.push(transferBuf);
		} else if (ArrayBuffer.isView(binArg)) {
			transferBuf = binArg.buffer.slice(binArg.byteOffset, binArg.byteOffset + binArg.byteLength);
			transferList.push(transferBuf);
		}
		return new Promise(function(resolve, reject) {
			var timer = setTimeout(function() {
				if (window._echoPendingReqs[reqId]) {
					delete window._echoPendingReqs[reqId];
					reject(new Error('echoRequest timeout (' + ttl + 'ms)'));
				}
			}, ttl);
			window._echoPendingReqs[reqId] = { resolve: resolve, reject: reject, timer: timer };
			postToHost(
				{ echoUIRequest: { window_id: WID, run_id: RUN_ID, action: action, payload: payload, req_id: reqId, binary: transferBuf } },
				transferList
			);
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

	const sendToFramePort = (wid: string, payload: any, transfer?: Transferable[]) => {
		const port = framePorts[wid];
		if (port) {
			if (transfer && transfer.length > 0) {
				port.postMessage(payload, transfer);
			} else {
				port.postMessage(payload);
			}
			return;
		}
		if (!pendingPortMessages[wid]) {
			pendingPortMessages[wid] = [];
		}
		pendingPortMessages[wid].push({ payload, transfer });
	};

	const closeAllFramePorts = () => {
		for (const wid of Object.keys(framePorts)) {
			try {
				framePorts[wid].close();
			} catch {
				// Ignorer
			}
		}
		framePorts = {};
		pendingPortMessages = {};
		iframeReady = {};
		srcdocCache = {};
	};

	const updateFramesFromWs = (
		targetChatId: string,
		updater: (current: EchoCanvasFrame[]) => EchoCanvasFrame[]
	) => {
		echoCanvasFrames.update((curr) => {
			const list = curr[targetChatId] || [];
			return { ...curr, [targetChatId]: updater(list) };
		});
	};

	const closeRunSocket = (runId: string) => {
		const entry = runSockets.get(runId);
		if (!entry) return;
		entry.closedByClient = true;
		if (entry.reconnectTimer) {
			clearTimeout(entry.reconnectTimer);
			entry.reconnectTimer = null;
		}
		if (entry.ws) {
			try {
				entry.ws.close(1000);
			} catch {
				// Ignorer
			}
			entry.ws = null;
		}
		runSockets.delete(runId);
	};

	const closeAllRunSockets = () => {
		for (const runId of Array.from(runSockets.keys())) {
			closeRunSocket(runId);
		}
	};

	const sendToRunSocket = (targetChatId: string, runId: string, payload: string | ArrayBuffer) => {
		if (!runId) return;
		let entry = runSockets.get(runId);
		if (!entry) {
			openRunSocket(targetChatId, runId);
			entry = runSockets.get(runId);
		}
		if (!entry) return;
		if (entry.ws && entry.ws.readyState === WebSocket.OPEN && entry.authed) {
			entry.ws.send(payload);
		} else {
			entry.outQueue.push(payload);
		}
	};

	const openRunSocket = (targetChatId: string, runId: string) => {
		if (!targetChatId || !runId || runSockets.has(runId)) return;
		const token = getEchoToken();
		if (!token) return;

		const entry: RunSocketEntry = {
			ws: null,
			authed: false,
			outQueue: [],
			reconnectTimer: null,
			closedByClient: false
		};
		runSockets.set(runId, entry);

		const ws = new WebSocket(getEchoCanvasWsUrl(targetChatId, runId));
		ws.binaryType = 'arraybuffer';
		entry.ws = ws;

		ws.onopen = () => {
			if (entry.closedByClient || entry.ws !== ws) return;
			ws.send(JSON.stringify({ type: 'auth', token }));
			entry.authed = true;
			while (entry.outQueue.length > 0 && ws.readyState === WebSocket.OPEN) {
				const item = entry.outQueue.shift();
				if (item !== undefined) {
					ws.send(item);
				}
			}
		};

		ws.onmessage = (ev: MessageEvent) => {
			if (entry.closedByClient) return;
			if (typeof ev.data === 'string') {
				let msg: any;
				try {
					msg = JSON.parse(ev.data);
				} catch {
					return;
				}
				if (!msg || typeof msg !== 'object') return;
				if (msg.type === 'ping') {
					if (ws.readyState === WebSocket.OPEN) {
						ws.send('{"type":"pong"}');
					}
					return;
				}
				if (msg.type === 'snapshot') {
					const jobState = String(msg.job_state || 'running');
					const isTerm = TERMINAL_STATUSES.has(jobState);
					if (Array.isArray(msg.frames) && msg.frames.length > 0) {
						updateFramesFromWs(targetChatId, (list) => {
							const next = [...list];
							for (const f of msg.frames) {
								const wid = String(f.id || f.window_id || 'default');
								const idx = next.findIndex((x) => x.id === wid);
								const updated: EchoCanvasFrame = {
									id: wid,
									title: String(f.title || 'Monitor'),
									html: String(f.html || ''),
									src: String(f.src || ''),
									width: String(f.width || '100%'),
									height: String(f.height || '400px'),
									run_id: runId,
									server_status: jobState,
									exit_code: msg.exit_code ?? f.exit_code ?? null,
									ready: Boolean(f.ready || (msg.ready_windows && msg.ready_windows[wid])),
									updated_at: f.updated_at || Date.now() / 1000
								};
								if (idx >= 0) {
									if (next[idx].html !== updated.html) delete srcdocCache[wid];
									next[idx] = { ...next[idx], ...updated };
								} else {
									next.push(updated);
								}
							}
							return next;
						});
					} else if (isTerm) {
						updateFramesFromWs(targetChatId, (list) =>
							list.map((f) =>
								f.run_id === runId
									? { ...f, server_status: jobState, exit_code: msg.exit_code ?? f.exit_code }
									: f
							)
						);
					}
					if (isTerm) {
						closeRunSocket(runId);
					}
					return;
				}
				if (msg.type === 'display') {
					const wid = String(msg.window_id || 'default');
					const nextHtml = String(msg.html || '');
					delete srcdocCache[wid];
					updateFramesFromWs(targetChatId, (list) => {
						const idx = list.findIndex((f) => f.id === wid);
						const frameObj: EchoCanvasFrame = {
							id: wid,
							title: String(msg.title || 'ECHO Sandbox Monitor'),
							html: nextHtml,
							src: '',
							width: String(msg.width || '100%'),
							height: String(msg.height || '400px'),
							run_id: runId,
							server_status: 'running',
							updated_at: Date.now() / 1000
						};
						if (idx >= 0) {
							const copy = [...list];
							copy[idx] = { ...copy[idx], ...frameObj };
							return copy;
						}
						return [...list, frameObj];
					});
					return;
				}
				if (msg.type === 'ready') {
					const wid = String(msg.window_id || 'default');
					updateFramesFromWs(targetChatId, (list) =>
						list.map((f) => (f.id === wid ? { ...f, ready: true } : f))
					);
					return;
				}
				if (msg.type === 'event') {
					const wid = String(msg.window_id || 'default');
					sendToFramePort(wid, {
						echoSandboxEvent: {
							data: msg.data,
							req_id: msg.req_id ?? null,
							binary: null
						}
					});
					return;
				}
				if (msg.type === 'status') {
					const jobState = String(msg.job_state || 'completed');
					const wids: string[] = Array.isArray(msg.window_ids)
						? msg.window_ids.map((w: any) => String(w))
						: [];
					const statusPayload = {
						server_status: jobState,
						exit_code: msg.exit_code ?? null,
						run_id: runId
					};
					updateFramesFromWs(targetChatId, (list) =>
						list.map((f) =>
							f.run_id === runId || wids.includes(f.id)
								? { ...f, server_status: jobState, exit_code: msg.exit_code ?? f.exit_code }
								: f
						)
					);
					const notifyWids = wids.length > 0
						? wids
						: frames.filter((f) => f.run_id === runId).map((f) => f.id);
					for (const wid of notifyWids) {
						sendToFramePort(wid, { echoSandboxStatus: statusPayload, window_id: wid });
					}
					if (TERMINAL_STATUSES.has(jobState)) {
						closeRunSocket(runId);
					}
				}
			} else if (ev.data instanceof ArrayBuffer && ev.data.byteLength >= 4) {
				try {
					const metaLen = new DataView(ev.data, 0, 4).getUint32(0, false);
					if (4 + metaLen > ev.data.byteLength) return;
					const metaText = new TextDecoder().decode(new Uint8Array(ev.data, 4, metaLen));
					const meta = JSON.parse(metaText);
					const binBuf = ev.data.slice(4 + metaLen);
					const wid = String(meta.window_id || 'default');
					sendToFramePort(
						wid,
						{
							echoSandboxEvent: {
								data: meta.data,
								req_id: meta.req_id ?? null,
								binary: binBuf
							}
						},
						[binBuf]
					);
				} catch {
					// Ignorer une trame binaire malformée
				}
			}
		};

		ws.onerror = () => {
			// Géré par onclose
		};

		ws.onclose = () => {
			if (entry.closedByClient) return;
			entry.ws = null;
			entry.authed = false;
			const stillRunning = frames.some(
				(f) => f.run_id === runId && !TERMINAL_STATUSES.has(f.server_status || '')
			);
			if (stillRunning && chatId === targetChatId) {
				entry.reconnectTimer = setTimeout(() => {
					runSockets.delete(runId);
					openRunSocket(targetChatId, runId);
				}, 1000);
			} else {
				runSockets.delete(runId);
			}
		};
	};

	const syncRunSockets = (targetChatId: string, currentFrames: EchoCanvasFrame[]) => {
		const runningRunIds = new Set(
			currentFrames
				.filter((f) => Boolean(f.run_id) && !TERMINAL_STATUSES.has(f.server_status || ''))
				.map((f) => f.run_id as string)
		);
		for (const existingRid of Array.from(runSockets.keys())) {
			if (!runningRunIds.has(existingRid)) {
				closeRunSocket(existingRid);
			}
		}
		for (const rid of runningRunIds) {
			if (!runSockets.has(rid)) {
				openRunSocket(targetChatId, rid);
			}
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

	const attachFramePort = (wid: string) => {
		const el = iframeRefs[wid];
		if (!el || !el.contentWindow) return;
		if (framePorts[wid]) {
			try {
				framePorts[wid].close();
			} catch {
				// Ignorer
			}
		}
		const channel = new MessageChannel();
		channel.port1.onmessage = (portEv: MessageEvent) => {
			void handlePortOrWindowMessage(wid, portEv);
		};
		channel.port1.start();
		framePorts[wid] = channel.port1;
		el.contentWindow.postMessage({ type: 'echo:port:attach', window_id: wid }, '*', [channel.port2]);

		const queued = pendingPortMessages[wid];
		if (queued && queued.length > 0) {
			delete pendingPortMessages[wid];
			for (const item of queued) {
				if (item.transfer && item.transfer.length > 0) {
					channel.port1.postMessage(item.payload, item.transfer);
				} else {
					channel.port1.postMessage(item.payload);
				}
			}
		}
	};

	const flushEventsForFrame = (wid: string) => {
		if (!chatId || !iframeReady[wid]) return;
		const events = drainEchoCanvasEvents(chatId, wid);
		for (const ev of events) {
			if (ev.isStatusEvent) {
				sendToFramePort(wid, {
					echoSandboxStatus: ev.data,
					window_id: wid
				});
			} else {
				sendToFramePort(wid, {
					echoSandboxEvent: {
						data: ev.data,
						req_id: ev.reqId ?? null,
						binary: null
					},
					window_id: wid
				});
			}
		}
	};

	const handleIframeReady = (wid: string) => {
		if (!iframeReady[wid]) {
			iframeReady[wid] = true;
			broadcastThemeToFrame(wid);
			attachFramePort(wid);
		}
		flushEventsForFrame(wid);
	};

	const handlePortOrWindowMessage = async (sourceWid: string, e: MessageEvent) => {
		const data = e.data;
		if (!data || typeof data !== 'object') return;

		const targetFrame = frames.find((f) => f.id === sourceWid);
		const effectiveRunId = targetFrame?.run_id || data.echoUIRequest?.run_id || '';

		if (data.echoUIRequest !== undefined && typeof data.echoUIRequest === 'object') {
			const reqObj = data.echoUIRequest;
			const binBuf = reqObj.binary instanceof ArrayBuffer ? reqObj.binary : null;
			const metaObj = {
				type: 'rpc',
				window_id: sourceWid,
				req_id: reqObj.req_id ?? null,
				action: reqObj.action,
				payload: reqObj.payload
			};
			if (effectiveRunId && chatId) {
				if (binBuf !== null) {
					const metaBytes = new TextEncoder().encode(JSON.stringify(metaObj));
					const packet = new ArrayBuffer(4 + metaBytes.byteLength + binBuf.byteLength);
					new DataView(packet, 0, 4).setUint32(0, metaBytes.byteLength, false);
					const outBytes = new Uint8Array(packet);
					outBytes.set(metaBytes, 4);
					outBytes.set(new Uint8Array(binBuf), 4 + metaBytes.byteLength);
					sendToRunSocket(chatId, effectiveRunId, packet);
				} else {
					sendToRunSocket(chatId, effectiveRunId, JSON.stringify(metaObj));
				}
			}
			return;
		}

		if (data.echoUIPayload !== undefined) {
			if (effectiveRunId && chatId) {
				const syncPayload =
					data.echoUIPayload && typeof data.echoUIPayload === 'object' && 'payload' in data.echoUIPayload
						? data.echoUIPayload.payload
						: data.echoUIPayload;
				sendToRunSocket(
					chatId,
					effectiveRunId,
					JSON.stringify({ type: 'payload_sync', window_id: sourceWid, payload: syncPayload })
				);
				return;
			}
			const token = getEchoToken();
			if (!token) return;
			try {
				await postEchoWmRpc(token, {
					action: 'canvas_ui_event',
					chat_id: chatId,
					frame_id: sourceWid,
					run_id: '',
					echoUIPayload: data.echoUIPayload
				});
			} catch {
				// Ignorer
			}
			return;
		}

		if (data.echoSignal === 'abort') {
			const token = getEchoToken();
			if (!token) return;
			try {
				await postEchoWmRpc(token, {
					action: 'canvas_ui_event',
					chat_id: chatId,
					frame_id: sourceWid,
					run_id: effectiveRunId,
					echoSignal: 'abort'
				});
			} catch {
				// Ignorer
			}
			return;
		}

		// Relais RPC venant de l'application Canvas vers le backend ECHO
		if (data.type === 'echo:rpc' || data.type === 'wm_rpc') {
			const token = getEchoToken();
			if (!token) return;
			const targetEl = iframeRefs[sourceWid];
			try {
				const response = await postEchoWmRpc(token, {
					chat_id: chatId,
					frame_id: sourceWid,
					...data.payload
				});
				if (targetEl && targetEl.contentWindow) {
					targetEl.contentWindow.postMessage(
						{ type: 'echo:rpc_response', request_id: data.request_id, response },
						'*'
					);
				}
			} catch (err: any) {
				if (targetEl && targetEl.contentWindow) {
					targetEl.contentWindow.postMessage(
						{ type: 'echo:rpc_error', request_id: data.request_id, error: err.message },
						'*'
					);
				}
			}
		}
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
		await handlePortOrWindowMessage(sourceWid, e);
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
		if (framePorts[frameId]) {
			try {
				framePorts[frameId].close();
			} catch {
				// Ignorer
			}
			delete framePorts[frameId];
		}
		delete pendingPortMessages[frameId];
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
			activeSocketChatId = chatId;
			void rehydrateCanvasFrames(chatId);
			syncRunSockets(chatId, frames);
		}
		themeObserver = new MutationObserver(() => {
			broadcastTheme();
		});
		themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('message', handleMessage);
		}
		closeAllRunSockets();
		closeAllFramePorts();
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
