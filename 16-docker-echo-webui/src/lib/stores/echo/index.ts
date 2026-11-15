import { writable } from 'svelte/store';
import { ECHO_API_BASE_URL, getEchoCognitionSnapshot } from '$lib/apis/echo';
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
	compaction_count?: number;
	compacted_turns?: number;
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
	created_at?: number;
	chat_id?: string;
	options?: Record<string, any>;
	[key: string]: any;
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

export interface EchoCognitionThreadMeta {
	sub_sid?: string;
	role_id?: string;
	role?: string;
	status?: string;
	updated_at?: number;
	thread_type?: 'web' | 'council' | 'supervisor' | 'expert' | 'agent';
	icon?: string;
	color?: string;
	display_label?: string;
	model_key?: string | null;
	is_running?: boolean;
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
	const token = localStorage?.token;
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
