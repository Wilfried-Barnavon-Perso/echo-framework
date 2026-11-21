import { writable } from 'svelte/store';
import { ECHO_API_BASE_URL, getEchoCognitionSnapshot, getEchoToken } from '$lib/apis/echo';
import { isSavedChatId } from '$lib/utils/chatId';

export interface EchoContextGaugePayload {
	chat_id?: string;
	max_tokens?: number;
	total_tokens?: number;
	percentage?: number;
	is_pending?: boolean;
	cached_tokens?: number;
	prompt_tokens?: number;
	generation_tokens?: number;
	delta_tokens?: number;
	delta_capacity?: number;
	compaction_count?: number;
	compacted_turns?: number;
	tenant_id?: string;
	plan_name?: string;
	credits_val?: string;
	quota_str?: string;
	totalTokenCount?: number;
	cachedContentTokenCount?: number;
	promptTokenCount?: number;
	candidatesTokenCount?: number;
	user_email?: string;
	user_tier?: string;
	project_id?: string;
	auth_sources?: string[];
	quota_fraction?: number;
	quota_reset?: string;
	quota_type?: string;
	quota_model?: string;
	quota_rpd_rem?: string | number;
	quota_rpd_lim?: string | number;
	quota_rpm_rem?: string | number;
	quota_rpm_lim?: string | number;
	quota_5h_fraction?: number | null;
	quota_5h_reset?: string;
	quota_5h_disabled?: boolean;
	quota_weekly_fraction?: number | null;
	quota_weekly_reset?: string;
	delta?: {
		delta_total?: number;
		capacity_lost_pct?: number;
		growth_pct?: number;
		delta_quota_5h?: number | null;
		delta_quota_weekly?: number | null;
		[key: string]: any;
	};
	quotas?: {
		five_hour_usage?: number;
		five_hour_limit?: number;
		five_hour_pct?: number;
		weekly_usage?: number;
		weekly_limit?: number;
		weekly_pct?: number;
		rpd_usage?: number;
		rpd_limit?: number;
		reset_seconds?: number;
		[key: string]: any;
	};
	[key: string]: any;
}

export interface EchoCanvasFrame {
	id: string;
	title?: string;
	html?: string;
	src?: string;
	width?: string;
	height?: string;
	run_id?: string;
	server_status?: 'running' | 'completed' | 'killed' | 'error' | 'timeout' | string;
	exit_code?: number | null;
	updated_at?: number;
	created_at?: number;
	chat_id?: string;
	options?: Record<string, any>;
	[key: string]: any;
}

export interface EchoCanvasEventPayload {
	chatId: string;
	windowId: string;
	data: any;
	reqId?: string | null;
	seq: number;
	isStatusEvent?: boolean;
	serverStatus?: string;
	exitCode?: number | null;
}

export interface EchoCognitionStep {
	sub_sid: string;
	step_index: number;
	role_id?: string;
	role: string;
	content: any;
	thought_signature?: string | null;
	updated_at?: number;
	origin?: 'orchestrator' | 'human' | 'tool';
	clean_text?: string;
	model_key?: string | null;
	escalation_target?: string | null;
	is_question?: boolean;
	pending_tool_calls?: boolean | { name: string; args?: any }[];
	participant_label?: string;
}

export type EchoSubagentStatus =
	| 'running'
	| 'idle'
	| 'completed'
	| 'pending_question'
	| 'suspended'
	| 'suspended_checkpoint'
	| 'suspended_pare_alert'
	| 'killed'
	| 'error';

export interface EchoCognitionThreadMeta {
	sub_sid?: string;
	role_id?: string;
	role?: string;
	role_name?: string;
	skill_id?: string | null;
	engine?:
		| 'delegate'
		| 'navigator'
		| 'council'
		| 'council_synthesis'
		| 'supervisor'
		| 'supervisor_critic'
		| 'supervisor_consolidator'
		| 'deep_research'
		| 'n8n_grapher'
		| 'data_broker'
		| 'image_gen'
		| string;
	status?: EchoSubagentStatus | string;
	slice_turns_used?: number;
	slice_turns_limit?: number;
	slice_timeout_sec?: number;
	total_turns_used?: number;
	slice_calls_used?: number;
	slice_calls_limit?: number;
	total_calls_used?: number;
	consecutive_errors?: number;
	checkpoint_reason?: string | null;
	group_id?: string | null;
	updated_at?: number;
	updated_at_ms?: number;
	thread_type?:
		| 'web'
		| 'council'
		| 'supervisor'
		| 'deep_research'
		| 'n8n_grapher'
		| 'data_broker'
		| 'image_gen'
		| 'expert'
		| 'agent';
	icon?: string;
	color?: string;
	display_label?: string;
	model_key?: string | null;
	is_running?: boolean;
	member_role?: 'participant' | 'synthesis' | 'worker' | 'critic' | 'consolidator' | 'agent' | string;
	role_order?: number;
	has_tool_loop?: boolean;
	is_addressable?: boolean;
	[key: string]: any;
}

export interface EchoCognitionQueuedMsg {
	id?: string;
	content: string;
	user_id?: string;
	created_at?: number;
	[key: string]: any;
}

export interface EchoCognitionGroup {
	id?: string;
	group_id?: string;
	thread_type?: string;
	group_type?: string;
	icon: string;
	color: string;
	display_label?: string;
	label?: string;
	member_sids?: string[];
	threads?: string[];
	participant_labels?: Record<string, string>;
	model_keys?: string[];
	group_status?: EchoSubagentStatus | string;
	is_running: boolean;
	updated_at: number;
	steps_count?: number;
	merged_steps?: EchoCognitionStep[];
}

export interface EchoCognitionSnapshot {
	chat_id: string;
	threads: Record<string, EchoCognitionStep[]>;
	thread_meta: Record<string, EchoCognitionThreadMeta>;
	grouped_threads?: EchoCognitionGroup[];
	queued_messages: Record<string, EchoCognitionQueuedMsg[]>;
	tool_calls: any[];
	active_count: number;
	running_count: number;
	total_count: number;
	updated_at: number;
}

export const ECHO_COGNITIVE_MODELS: Record<
	string,
	{ key: string; label: string; icon: string; badgeClass: string }
> = {
	MODEL_ZEPHYR: {
		key: 'MODEL_ZEPHYR',
		label: 'Zéphyr',
		icon: '⚡',
		badgeClass: 'bg-sky-500/15 text-sky-400 border-sky-500/30'
	},
	MODEL_APOLLO: {
		key: 'MODEL_APOLLO',
		label: 'Apollon',
		icon: '☀️',
		badgeClass: 'bg-amber-500/15 text-amber-400 border-amber-500/30'
	},
	MODEL_PROMETHEUS: {
		key: 'MODEL_PROMETHEUS',
		label: 'Prométhée',
		icon: '🔥',
		badgeClass: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
	},
	MODEL_ATHENA: {
		key: 'MODEL_ATHENA',
		label: 'Athéna',
		icon: '🦉',
		badgeClass: 'bg-purple-500/15 text-purple-400 border-purple-500/30'
	}
};

export function getEchoModelBadge(modelKey?: string | null) {
	if (!modelKey) return null;
	return ECHO_COGNITIVE_MODELS[modelKey] ?? null;
}

export type EchoTabKind =
	| 'echo-cognition'
	| 'echo-codex'
	| 'echo-browser'
	| 'echo-canvas'
	| 'echo-vault';

export interface EchoTabOpenRequest {
	chatId: string;
	kind: EchoTabKind;
	payload?: {
		subSid?: string;
		frameId?: string;
		workspace?: string;
		file?: string;
		commitHash?: string;
		targetChatId?: string;
		sessionId?: string;
		frameIndex?: number;
		auto?: boolean;
		[key: string]: any;
	};
}

export const echoContextGauge = writable<EchoContextGaugePayload | null>(null);
export const echoCognitionActiveCount = writable<number>(0);
export const echoCognitionRunningCount = writable<number>(0);
export const echoCognitionSnapshot = writable<EchoCognitionSnapshot | null>(null);
export const echoCognitionSelectedSubSid = writable<Record<string, string>>({});
export const echoTabOpenRequest = writable<EchoTabOpenRequest | null>(null);
export const echoCodexTarget = writable<{
	chatId: string;
	workspace?: string;
	file?: string;
	commitHash?: string;
	targetChatId?: string;
} | null>(null);
export const echoBrowserTarget = writable<{
	chatId: string;
	sessionId?: string;
	frameId?: string;
	frameIndex?: number;
} | null>(null);
export const echoBrowserLiveFrames = writable<Record<string, any[]>>({});
export const echoCanvasFrames = writable<Record<string, EchoCanvasFrame[]>>({});
export const echoCanvasActiveFrame = writable<Record<string, string>>({});
export const echoCanvasLastEvent = writable<EchoCanvasEventPayload | null>(null);
export const echoCanvasEventQueues = writable<Record<string, EchoCanvasEventPayload[]>>({});
let canvasEventSeq = 0;

const canvasQueueKey = (chatId: string, windowId: string): string => `${chatId}::${windowId}`;

export const enqueueEchoCanvasEvent = (ev: EchoCanvasEventPayload): void => {
	const key = canvasQueueKey(ev.chatId, ev.windowId);
	echoCanvasEventQueues.update((queues) => {
		const existing = queues[key] || [];
		return { ...queues, [key]: [...existing, ev].slice(-200) };
	});
};

export const drainEchoCanvasEvents = (chatId: string, windowId: string): EchoCanvasEventPayload[] => {
	const key = canvasQueueKey(chatId, windowId);
	let drained: EchoCanvasEventPayload[] = [];
	echoCanvasEventQueues.update((queues) => {
		if (!queues[key] || queues[key].length === 0) return queues;
		drained = queues[key];
		const next = { ...queues };
		delete next[key];
		return next;
	});
	return drained;
};

export const hydrateEchoCanvasFrames = (chatId: string, rawFrames: any[]): void => {
	if (!chatId || !Array.isArray(rawFrames) || rawFrames.length === 0) return;
	const TERMINAL_STATUSES = new Set(['completed', 'killed', 'error', 'timeout']);
	echoCanvasFrames.update((all) => {
		const list = [...(all[chatId] || [])];
		for (const raw of rawFrames) {
			if (!raw || typeof raw !== 'object') continue;
			const id = String(raw.id || raw.frame_id || raw.window_id || '');
			if (!id) continue;
			const incomingUpdated = typeof raw.updated_at === 'number' ? raw.updated_at : 0;
			const idx = list.findIndex((f) => f.id === id);
			if (idx >= 0) {
				const localUpdated = typeof list[idx].updated_at === 'number' ? list[idx].updated_at! : 0;
				const isTerminalReconciliation =
					TERMINAL_STATUSES.has(String(raw.server_status || '')) &&
					!TERMINAL_STATUSES.has(String(list[idx].server_status || ''));
				if (localUpdated > incomingUpdated && !isTerminalReconciliation) continue;
				list[idx] = {
					...list[idx],
					...raw,
					id,
					run_id: raw.run_id ?? list[idx].run_id ?? '',
					server_status: raw.server_status ?? list[idx].server_status ?? 'running',
					exit_code: raw.exit_code ?? list[idx].exit_code ?? null,
					updated_at: incomingUpdated || localUpdated
				};
			} else {
				list.push({
					id,
					title: raw.title || raw.name || 'Application',
					html: raw.html || raw.code || '',
					src: raw.src || '',
					width: raw.width || '100%',
					height: raw.height || '100%',
					run_id: raw.run_id || '',
					server_status: raw.server_status || 'running',
					exit_code: raw.exit_code ?? null,
					updated_at: incomingUpdated,
					created_at: Date.now(),
					options: raw.options || {}
				});
			}
		}
		return { ...all, [chatId]: list };
	});
};

export const removeEchoCanvasFrames = (chatId: string, frameIds?: string[]): void => {
	if (!chatId) return;
	const idSet = Array.isArray(frameIds) ? new Set(frameIds.map(String)) : null;
	echoCanvasFrames.update((all) => {
		const list = all[chatId] || [];
		if (list.length === 0) return all;
		if (!idSet) {
			const next = { ...all };
			delete next[chatId];
			return next;
		}
		const remaining = list.filter((f) => !idSet.has(f.id));
		if (remaining.length === 0) {
			const next = { ...all };
			delete next[chatId];
			return next;
		}
		return { ...all, [chatId]: remaining };
	});
	echoCanvasEventQueues.update((queues) => {
		const prefix = `${chatId}::`;
		let changed = false;
		const next = { ...queues };
		for (const key of Object.keys(next)) {
			if (!key.startsWith(prefix)) continue;
			const wid = key.slice(prefix.length);
			if (!idSet || idSet.has(wid)) {
				delete next[key];
				changed = true;
			}
		}
		return changed ? next : queues;
	});
	echoCanvasActiveFrame.update((curr) => {
		const active = curr[chatId];
		if (!active) return curr;
		if (!idSet || idSet.has(active)) {
			const next = { ...curr };
			delete next[chatId];
			return next;
		}
		return curr;
	});
};
export const requestEchoTabOpen = (
	chatId: string,
	kind: EchoTabKind,
	payload?: EchoTabOpenRequest['payload']
) => {
	if (!chatId) return;
	echoTabOpenRequest.set({ chatId, kind, payload });
};
export const echoEdgeEmbedState = writable<
	'idle' | 'probing' | 'downloading' | 'ready' | 'active' | 'incompatible'
>('idle');
export const echoEdgeEmbedProgress = writable<number>(0);
export const echoEdgeEmbedReason = writable<string>('');
export const echoEdgeEmbedDtype = writable<'fp16' | 'fp32'>('fp16');
export const ECHO_EDGE_TARGET_REPO = 'onnx-community/harrier-oss-v1-0.6b-ONNX';

// Gestionnaire SSE refcounté unique pour /echo/v1/cognition/{chatId}/stream
let activeStreamChatId = '';
let activeRefCount = 0;
let activeEventSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

const clearReconnectTimer = () => {
	if (reconnectTimer !== null) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}
};

const closeActiveStream = () => {
	clearReconnectTimer();
	if (activeEventSource) {
		activeEventSource.close();
		activeEventSource = null;
	}
};

const resetCognitionStores = () => {
	echoCognitionSnapshot.set(null);
	echoCognitionActiveCount.set(0);
	echoCognitionRunningCount.set(0);
};

const applyCognitionSnapshot = (data: EchoCognitionSnapshot) => {
	if (!data || typeof data !== 'object') return;
	const threads = data.threads ?? {};
	echoCognitionSnapshot.set(data);
	echoCognitionActiveCount.set(data.total_count ?? Object.keys(threads).length);
	echoCognitionRunningCount.set(data.running_count ?? 0);
};

const openCognitionEventSource = (chatId: string) => {
	if (typeof window === 'undefined') return;
	const token = localStorage?.token;
	if (!token || !isSavedChatId(chatId)) return;

	closeActiveStream();
	const url = `${ECHO_API_BASE_URL}/cognition/${encodeURIComponent(chatId)}/stream?token=${encodeURIComponent(token)}`;
	const source = new EventSource(url);
	activeEventSource = source;

	source.onmessage = (event) => {
		if (chatId !== activeStreamChatId) return;
		try {
			const data = JSON.parse(event.data) as EchoCognitionSnapshot;
			if (!data?.chat_id || data.chat_id === activeStreamChatId) {
				applyCognitionSnapshot(data);
			}
		} catch {
			// Ignorer les trames JSON malformées
		}
	};

	source.onerror = () => {
		if (activeEventSource === source) {
			source.close();
			activeEventSource = null;
		}
		if (activeRefCount > 0 && activeStreamChatId === chatId) {
			clearReconnectTimer();
			reconnectTimer = setTimeout(() => {
				reconnectTimer = null;
				if (activeRefCount > 0 && activeStreamChatId === chatId) {
					openCognitionEventSource(chatId);
				}
			}, 3000);
		}
	};
};

export const acquireCognitionStream = (chatId: string): (() => void) => {
	if (typeof window === 'undefined') return () => {};

	const token = localStorage?.token;
	if (!chatId || !isSavedChatId(chatId) || !token) {
		closeActiveStream();
		activeStreamChatId = '';
		activeRefCount = 0;
		resetCognitionStores();
		return () => {};
	}

	if (activeStreamChatId !== chatId) {
		closeActiveStream();
		activeStreamChatId = chatId;
		activeRefCount = 0;
		resetCognitionStores();
	}

	activeRefCount += 1;
	if (!activeEventSource) {
		openCognitionEventSource(chatId);
	}

	let released = false;
	return () => {
		if (released) return;
		released = true;
		if (activeStreamChatId !== chatId) return;
		activeRefCount = Math.max(0, activeRefCount - 1);
		if (activeRefCount === 0) {
			closeActiveStream();
			activeStreamChatId = '';
			resetCognitionStores();
		}
	};
};

export const refreshCognitionSnapshot = async (
	chatId: string
): Promise<EchoCognitionSnapshot | null> => {
	if (typeof window === 'undefined') return null;
	const token = getEchoToken();
	if (!token || !chatId || !isSavedChatId(chatId)) return null;

	try {
		const data = (await getEchoCognitionSnapshot(token, chatId)) as EchoCognitionSnapshot;
		if (activeStreamChatId === '' || activeStreamChatId === chatId) {
			applyCognitionSnapshot(data);
		}
		return data;
	} catch {
		return null;
	}
};

// ============================================================================
// Gestionnaire SSE global unique pour /echo/v1/wm/stream
// ============================================================================
let wmEventSource: EventSource | null = null;
let wmReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let wmActive = false;
let wmGetActiveChatId: () => string = () => '';

const clearWmReconnectTimer = () => {
	if (wmReconnectTimer !== null) {
		clearTimeout(wmReconnectTimer);
		wmReconnectTimer = null;
	}
};

const closeWmEventSource = () => {
	clearWmReconnectTimer();
	if (wmEventSource) {
		wmEventSource.close();
		wmEventSource = null;
	}
};

const openWmEventSource = () => {
	if (typeof window === 'undefined' || !wmActive) return;
	const token = getEchoToken();
	if (!token) return;

	closeWmEventSource();
	const wmUrl = `${ECHO_API_BASE_URL}/wm/stream?token=${encodeURIComponent(token)}`;
	const source = new EventSource(wmUrl);
	wmEventSource = source;

	source.onmessage = (event) => {
		try {
			const data = JSON.parse(event.data);
			if (!data || typeof data !== 'object') return;

			if (data.type === 'echo_context_gauge' && data.payload) {
				const activeCid = wmGetActiveChatId();
				if (!data.payload.chat_id || data.payload.chat_id === activeCid) {
					echoContextGauge.set(data.payload);
				}
			} else if (data.type === 'echo_open_tab') {
				const cid = data.chat_id || wmGetActiveChatId() || '';
				if (cid && data.kind) {
					requestEchoTabOpen(cid, data.kind, data.payload);
				}
			} else if (data.type === 'echo_browser_frame') {
				const cid = data.chat_id || wmGetActiveChatId() || '';
				const frame = data.frame || data.payload || data;
				const frameKey = frame?.file_id;
				if (cid && frame && frameKey) {
					echoBrowserLiveFrames.update((all) => {
						const list = all[cid] || [];
						const idx = list.findIndex((f) => f.file_id === frameKey);
						const next =
							idx >= 0
								? list.map((f, i) => (i === idx ? { ...f, ...frame } : f))
								: [...list, frame].slice(-50);
						return { ...all, [cid]: next };
					});
				}
			} else if (data.type === 'window_mounted') {
				const cid = data.chat_id || wmGetActiveChatId() || '';
				const frame: EchoCanvasFrame = {
					id: String(data.id || data.frame_id || data.window_id || crypto.randomUUID()),
					title: data.title || data.name || 'Application',
					html: data.html || data.code || '',
					src: data.src || '',
					width: data.width || '100%',
					height: data.height || '100%',
					run_id: data.run_id || '',
					server_status: data.server_status || 'running',
					exit_code: data.exit_code ?? null,
					updated_at: typeof data.updated_at === 'number' ? data.updated_at : Date.now() / 1000,
					created_at: Date.now(),
					options: data.options || {}
				};
				if (cid) {
					echoCanvasFrames.update((all) => {
						const list = all[cid] || [];
						const idx = list.findIndex((f) => f.id === frame.id);
						const next = idx >= 0 ? list.map((f, i) => (i === idx ? { ...f, ...frame } : f)) : [...list, frame];
						return { ...all, [cid]: next };
					});
					echoCanvasActiveFrame.update((curr) => ({ ...curr, [cid]: frame.id }));
					requestEchoTabOpen(cid, 'echo-canvas', { frameId: frame.id, auto: data.auto ?? true });
				}
			} else if (data.type === 'echo_canvas_event') {
				const cid = data.chat_id || wmGetActiveChatId() || '';
				if (cid) {
					canvasEventSeq += 1;
					const ev: EchoCanvasEventPayload = {
						chatId: cid,
						windowId: String(data.window_id || 'default'),
						data: data.data,
						reqId: data.req_id ?? null,
						seq: canvasEventSeq
					};
					enqueueEchoCanvasEvent(ev);
					echoCanvasLastEvent.set(ev);
				}
			} else if (data.type === 'echo_canvas_status') {
				const cid = data.chat_id || wmGetActiveChatId() || '';
				const wids: string[] = Array.isArray(data.window_ids)
					? data.window_ids.map(String).filter(Boolean)
					: [];
				if (cid && wids.length > 0) {
					const serverStatus = data.server_status || 'completed';
					const exitCode = data.exit_code ?? null;
					const runId = data.run_id || '';
					echoCanvasFrames.update((all) => {
						const list = all[cid] || [];
						if (list.length === 0) return all;
						const next = list.map((f) => {
							if (wids.includes(f.id) && (!runId || !f.run_id || f.run_id === runId)) {
								return {
									...f,
									server_status: serverStatus,
									exit_code: exitCode
								};
							}
							return f;
						});
						return { ...all, [cid]: next };
					});
					for (const wid of wids) {
						canvasEventSeq += 1;
						enqueueEchoCanvasEvent({
							chatId: cid,
							windowId: wid,
							data: {
								server_status: serverStatus,
								exit_code: exitCode,
								run_id: runId
							},
							reqId: null,
							seq: canvasEventSeq,
							isStatusEvent: true,
							serverStatus,
							exitCode
						});
					}
				}
			}
		} catch {
			// Ignorer les trames JSON malformées
		}
	};

	source.onerror = () => {
		if (wmEventSource === source) {
			source.close();
			wmEventSource = null;
		}
		if (wmActive) {
			clearWmReconnectTimer();
			wmReconnectTimer = setTimeout(() => {
				wmReconnectTimer = null;
				if (wmActive) {
					openWmEventSource();
				}
			}, 3000);
		}
	};
};

export const stopEchoWmStream = (): void => {
	wmActive = false;
	closeWmEventSource();
};

export const startEchoWmStream = (getActiveChatId: () => string): (() => void) => {
	if (typeof window === 'undefined') return () => {};
	wmGetActiveChatId = getActiveChatId;
	const token = getEchoToken();
	if (!token) {
		stopEchoWmStream();
		return () => {};
	}

	wmActive = true;
	if (!wmEventSource) {
		openWmEventSource();
	}

	return () => {
		stopEchoWmStream();
	};
};

// Gestionnaire singleton WebGPU Edge Embedding (/ws/edge-embed)
const EDGE_MIN_BUFFER_BYTES = 134217728; // 128 Mo
const EDGE_PROBE_TIMEOUT_MS = 3500;
const EDGE_WS_RECONNECT_MS = 5000;

let edgeActive = false;
let edgeWs: WebSocket | null = null;
let edgeReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let edgeClientId = '';
let edgeTokenizer: any = null;
let edgeModel: any = null;
let edgeInitPromise: Promise<void> | null = null;
let edgeVisibilityBound = false;

const makeEdgeClientId = (): string => {
	try {
		if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
			return crypto.randomUUID();
		}
	} catch {
		// fallback
	}
	return 'edge-' + Math.random().toString(36).slice(2) + '-' + Date.now().toString(36);
};

const withEdgeTimeout = <T>(promise: Promise<T>, ms: number, label: string): Promise<T> => {
	return new Promise<T>((resolve, reject) => {
		const timer = setTimeout(() => {
			reject(new Error(`Timeout WebGPU (${label}, ${ms} ms)`));
		}, ms);
		promise.then(
			(val) => {
				clearTimeout(timer);
				resolve(val);
			},
			(err) => {
				clearTimeout(timer);
				reject(err);
			}
		);
	});
};

const buildEdgeWsUrl = (): string => {
	const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
	const token = getEchoToken();
	const tokenParam = token ? `&token=${encodeURIComponent(token)}` : '';
	return `${protocol}//${location.host}/ws/edge-embed?client_id=${encodeURIComponent(edgeClientId)}${tokenParam}`;
};

const notifyEdgeBackendIncompatible = (reason: string): void => {
	if (typeof window === 'undefined') return;
	try {
		const quickWs = new WebSocket(buildEdgeWsUrl());
		quickWs.onopen = () => {
			try {
				quickWs.send(JSON.stringify({ type: 'incompatible', reason }));
			} catch {
				// ignore
			}
			setTimeout(() => {
				try {
					quickWs.close();
				} catch {
					// ignore
				}
			}, 150);
		};
		quickWs.onerror = () => {
			try {
				quickWs.close();
			} catch {
				// ignore
			}
		};
	} catch {
		// ignore
	}
};

const probeEchoWebGPU = async (): Promise<{
	compatible: boolean;
	dtype?: 'fp16' | 'fp32';
	reason?: string;
}> => {
	if (typeof window === 'undefined' || !window.isSecureContext) {
		return {
			compatible: false,
			reason: 'Contexte non sécurisé (HTTPS ou localhost requis pour WebGPU)'
		};
	}
	if (typeof navigator === 'undefined' || !(navigator as any).gpu) {
		return { compatible: false, reason: 'WebGPU non exposé par le navigateur' };
	}

	const gpu = (navigator as any).gpu;
	let adapter: any = null;
	let lastErr: string | undefined;

	try {
		adapter = await withEdgeTimeout(
			gpu.requestAdapter({ powerPreference: 'high-performance' }),
			EDGE_PROBE_TIMEOUT_MS,
			'requestAdapter(high-performance)'
		);
	} catch (e: any) {
		lastErr = e?.message;
		adapter = null;
	}

	if (!adapter) {
		try {
			adapter = await withEdgeTimeout(
				gpu.requestAdapter(),
				EDGE_PROBE_TIMEOUT_MS,
				'requestAdapter()'
			);
		} catch (e: any) {
			return {
				compatible: false,
				reason: e?.message || lastErr || 'Erreur lors de requestAdapter'
			};
		}
	}

	if (!adapter) {
		return { compatible: false, reason: lastErr || 'Adaptateur WebGPU refusé' };
	}

	if (adapter.isFallbackAdapter || (adapter.info && adapter.info.isFallbackAdapter)) {
		return { compatible: false, reason: 'Adaptateur WebGPU logiciel (fallback) rejeté' };
	}

	const hasFp16 = Boolean(adapter.features && adapter.features.has('shader-f16'));
	const dtype: 'fp16' | 'fp32' = hasFp16 ? 'fp16' : 'fp32';

	const limits = adapter.limits || {};
	if (
		(limits.maxBufferSize && limits.maxBufferSize < EDGE_MIN_BUFFER_BYTES) ||
		(limits.maxStorageBufferBindingSize &&
			limits.maxStorageBufferBindingSize < EDGE_MIN_BUFFER_BYTES)
	) {
		return { compatible: false, reason: 'Limites de buffer VRAM insuffisantes (< 128 Mo)' };
	}

	try {
		const device: any = await withEdgeTimeout(
			adapter.requestDevice({ requiredFeatures: hasFp16 ? ['shader-f16'] : [] }),
			EDGE_PROBE_TIMEOUT_MS,
			'requestDevice'
		);
		if (!device) {
			return { compatible: false, reason: 'Allocation GPUDevice refusée' };
		}
		device.destroy();
	} catch (e: any) {
		return {
			compatible: false,
			reason: `Allocation GPUDevice (${dtype}) échouée: ${e?.message || 'erreur inconnue'}`
		};
	}

	return { compatible: true, dtype };
};

const clearEdgeReconnectTimer = (): void => {
	if (edgeReconnectTimer !== null) {
		clearTimeout(edgeReconnectTimer);
		edgeReconnectTimer = null;
	}
};

const closeEdgeWebSocket = (): void => {
	clearEdgeReconnectTimer();
	if (edgeWs) {
		const sock = edgeWs;
		edgeWs = null;
		try {
			sock.onopen = null;
			sock.onmessage = null;
			sock.onerror = null;
			sock.onclose = null;
			sock.close();
		} catch {
			// ignore
		}
	}
};

const openEdgeWebSocket = (): void => {
	if (!edgeActive || typeof window === 'undefined') return;
	closeEdgeWebSocket();

	const sock = new WebSocket(buildEdgeWsUrl());
	edgeWs = sock;

	sock.onopen = () => {
		if (!edgeActive || edgeWs !== sock) {
			try {
				sock.close();
			} catch {
				// ignore
			}
			return;
		}
		try {
			sock.send(JSON.stringify({ type: 'ready' }));
			if (typeof document !== 'undefined' && !document.hidden) {
				sock.send(JSON.stringify({ type: 'visibility', state: 'active' }));
			}
		} catch {
			// ignore
		}
		echoEdgeEmbedState.set('ready');
	};

	sock.onmessage = async (event) => {
		if (!edgeActive || edgeWs !== sock) return;
		try {
			const payload = JSON.parse(event.data);
			if (payload.type === 'embed' && payload.texts && payload.request_id) {
				if (!edgeTokenizer || !edgeModel) return;
				echoEdgeEmbedState.set('active');
				const texts = Array.isArray(payload.texts) ? payload.texts : [payload.texts];
				const inputs = edgeTokenizer(texts, { padding: true, truncation: true });
				const outputs = await edgeModel(inputs);
				if (!edgeActive || edgeWs !== sock) return;

				const tensor =
					outputs.sentence_embedding || outputs.last_hidden_state || Object.values(outputs)[0];
				const dims = tensor.dims;
				const batchSize = dims[0];
				const is3D = dims.length >= 3;
				const seqLen = is3D ? dims[1] : 1;
				const embedDim = is3D ? dims[2] : dims[1];

				const embeddings: number[][] = [];
				for (let i = 0; i < batchSize; i++) {
					const vecStart = is3D ? i * seqLen * embedDim : i * embedDim;
					const vecEnd = vecStart + embedDim;
					const vector = tensor.data.slice(vecStart, vecEnd);

					let sumSq = 0;
					for (let j = 0; j < embedDim; j++) sumSq += vector[j] * vector[j];
					const norm = Math.sqrt(sumSq) || 1;

					const normVec = new Array(embedDim);
					for (let j = 0; j < embedDim; j++) {
						const val = Number(vector[j]);
						normVec[j] = (isNaN(val) ? 0 : val) / norm;
					}
					embeddings.push(normVec);
				}

				if (sock.readyState === WebSocket.OPEN) {
					sock.send(
						JSON.stringify({
							type: 'result',
							request_id: payload.request_id,
							embedding: embeddings
						})
					);
				}
				echoEdgeEmbedState.set('ready');
			}
		} catch (e: any) {
			console.error('Erreur traitement embedding WebGPU:', e);
			if (edgeActive) {
				echoEdgeEmbedState.set('ready');
			}
		}
	};

	sock.onerror = (e) => {
		console.warn('Erreur WebSocket Edge Embed:', e);
	};

	sock.onclose = () => {
		if (edgeWs === sock) {
			edgeWs = null;
		}
		if (edgeActive && edgeModel && edgeTokenizer && edgeReconnectTimer === null) {
			edgeReconnectTimer = setTimeout(() => {
				edgeReconnectTimer = null;
				if (edgeActive && edgeModel && edgeTokenizer) {
					openEdgeWebSocket();
				}
			}, EDGE_WS_RECONNECT_MS);
		}
	};
};

const handleEdgeVisibilityChange = (): void => {
	if (!edgeActive) return;
	if (edgeWs && edgeWs.readyState === WebSocket.OPEN) {
		try {
			const state = document.hidden ? 'idle' : 'active';
			edgeWs.send(JSON.stringify({ type: 'visibility', state }));
		} catch {
			// ignore
		}
		return;
	}
	if (typeof document !== 'undefined' && !document.hidden && !edgeWs && edgeModel && edgeTokenizer) {
		openEdgeWebSocket();
	}
};

export const stopEchoEdgeEmbedding = (): void => {
	edgeActive = false;
	if (typeof document !== 'undefined' && edgeVisibilityBound) {
		document.removeEventListener('visibilitychange', handleEdgeVisibilityChange);
		edgeVisibilityBound = false;
	}
	closeEdgeWebSocket();
};

export const startEchoEdgeEmbedding = (isMobile: () => boolean = () => false): (() => void) => {
	if (typeof window === 'undefined') return () => {};
	if (edgeActive || edgeInitPromise) {
		return () => stopEchoEdgeEmbedding();
	}

	if (isMobile()) {
		echoEdgeEmbedState.set('incompatible');
		echoEdgeEmbedReason.set('Appareil mobile détecté');
		return () => {};
	}

	edgeActive = true;
	if (!edgeClientId) {
		edgeClientId = makeEdgeClientId();
	}

	if (typeof document !== 'undefined' && !edgeVisibilityBound) {
		document.addEventListener('visibilitychange', handleEdgeVisibilityChange);
		edgeVisibilityBound = true;
	}

	echoEdgeEmbedState.set('probing');
	echoEdgeEmbedProgress.set(0);
	echoEdgeEmbedReason.set('');

	edgeInitPromise = (async () => {
		let currentState: 'probing' | 'downloading' | 'ready' | 'incompatible' = 'probing';
		try {
			const probe = await probeEchoWebGPU();
			if (!edgeActive) return;

			if (!probe.compatible) {
				const reason = probe.reason || 'WebGPU incompatible';
				currentState = 'incompatible';
				echoEdgeEmbedReason.set(reason);
				echoEdgeEmbedState.set('incompatible');
				notifyEdgeBackendIncompatible(reason);
				return;
			}

			const chosenDtype = probe.dtype || 'fp16';
			echoEdgeEmbedDtype.set(chosenDtype);
			currentState = 'downloading';
			echoEdgeEmbedState.set('downloading');
			echoEdgeEmbedProgress.set(0);

			if (!edgeTokenizer || !edgeModel) {
				const transformers: any = await import('@huggingface/transformers');
				if (!edgeActive) return;

				if (transformers?.env) {
					transformers.env.allowLocalModels = false;
					if (transformers.env.backends?.onnx?.wasm) {
						transformers.env.backends.onnx.wasm.wasmPaths = '/wasm/';
					}
				}

				if (!edgeTokenizer) {
					edgeTokenizer = await transformers.AutoTokenizer.from_pretrained(ECHO_EDGE_TARGET_REPO);
					if (!edgeActive) return;
				}

				if (!edgeModel) {
					edgeModel = await transformers.AutoModel.from_pretrained(ECHO_EDGE_TARGET_REPO, {
						device: 'webgpu',
						dtype: chosenDtype,
						progress_callback: (x: any) => {
							if (!edgeActive) return;
							if (x.status === 'downloading' || x.status === 'progress') {
								const pct = x.total ? Math.round((x.loaded / x.total) * 100) : 0;
								echoEdgeEmbedProgress.set(pct);
							}
						}
					});
					if (!edgeActive) return;
				}
			}

			echoEdgeEmbedProgress.set(100);
			echoEdgeEmbedReason.set('');
			currentState = 'ready';
			echoEdgeEmbedState.set('ready');

			openEdgeWebSocket();
		} catch (err: any) {
			if (!edgeActive) return;
			console.warn('Échec initialisation Edge Embedding WebGPU:', err);
			const reason = err?.message || 'Échec initialisation WebGPU';
			currentState = 'incompatible';
			echoEdgeEmbedReason.set(reason);
			echoEdgeEmbedState.set('incompatible');
			notifyEdgeBackendIncompatible(reason);
		} finally {
			edgeInitPromise = null;
			if (!edgeActive && currentState === 'probing') {
				echoEdgeEmbedState.set('idle');
			}
		}
	})();

	return () => {
		stopEchoEdgeEmbedding();
	};
};
