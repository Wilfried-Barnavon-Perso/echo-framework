import { echoRequestJson } from '$lib/apis/echo';

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
