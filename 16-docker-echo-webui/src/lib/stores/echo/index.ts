import { writable } from 'svelte/store';

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

export const echoContextGauge = writable<EchoContextGaugePayload | null>(null);
export const echoCognitionActiveCount = writable<number>(0);
export const echoCanvasFrames = writable<Record<string, EchoCanvasFrame[]>>({});
export const echoCanvasOpenRequest = writable<{ chatId: string; frameId?: string } | null>(null);
export const echoEdgeEmbedState = writable<
	'idle' | 'probing' | 'downloading' | 'ready' | 'active' | 'incompatible'
>('idle');
