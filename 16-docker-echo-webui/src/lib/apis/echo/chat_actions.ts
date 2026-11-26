import { echoRequestJson, getEchoToken } from '$lib/apis/echo';

export interface ResumeChatRagReport {
	complete: boolean;
	cloned: number;
	eligible: number;
	error: string | null;
}

export interface ResumeChatResponse {
	status: string;
	new_chat_id: string;
	title: string;
	rag_report: ResumeChatRagReport;
}

export interface EchoCompactionCheckpoint {
	checkpoint_id: string;
	compaction_count: number;
	pivot_turn_idx: number;
	pivot_msg_id: string | null;
	head_last_msg_id?: string | null;
	tail_anchor_ids?: string[];
	keep_recent_turns?: number;
	checkpoint_tokens?: number;
	branch_index?: number | null;
	created_at: number;
	[key: string]: any;
}

export interface EchoChatWindowResponse {
	messages: Record<string, any>;
	has_older: boolean;
	oldest_loaded_id: string | null;
	checkpoints: EchoCompactionCheckpoint[];
	tree_index: Record<string, any>;
}

export interface EchoMessageDetailsResponse {
	messages: Record<string, any>;
}

export const resumeChatInNewSession = async (
	token: string,
	chatId: string
): Promise<ResumeChatResponse> =>
	echoRequestJson<ResumeChatResponse>(
		`/chats/${encodeURIComponent(chatId)}/resume`,
		token,
		{ method: 'POST' },
		'Impossible de poursuivre cette conversation dans un nouveau chat'
	);

export const getEchoChatWindow = async (
	chatId: string,
	params?: {
		beforeId?: string;
		toCheckpointId?: string;
		targetId?: string;
		limit?: number;
	},
	token: string = getEchoToken()
): Promise<EchoChatWindowResponse> => {
	const searchParams = new URLSearchParams();
	if (params?.beforeId) searchParams.set('before_id', params.beforeId);
	if (params?.toCheckpointId) searchParams.set('to_checkpoint_id', params.toCheckpointId);
	if (params?.targetId) searchParams.set('target_id', params.targetId);
	if (typeof params?.limit === 'number' && params.limit > 0) {
		searchParams.set('limit', String(params.limit));
	}
	const qs = searchParams.toString();
	return echoRequestJson<EchoChatWindowResponse>(
		`/chats/${encodeURIComponent(chatId)}/window${qs ? `?${qs}` : ''}`,
		token,
		{ method: 'GET' },
		'Impossible de charger la tranche de conversation'
	);
};

export const fetchEchoMessageDetails = async (
	chatId: string,
	messageIds: string[],
	token: string = getEchoToken()
): Promise<EchoMessageDetailsResponse> =>
	echoRequestJson<EchoMessageDetailsResponse>(
		`/chats/${encodeURIComponent(chatId)}/messages/details`,
		token,
		{
			method: 'POST',
			body: JSON.stringify({ message_ids: messageIds })
		},
		'Impossible de charger les détails du message'
	);
