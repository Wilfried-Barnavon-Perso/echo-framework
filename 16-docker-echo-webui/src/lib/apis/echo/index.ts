import { WEBUI_BASE_URL } from '$lib/constants';
import type { EchoCognitionSnapshot } from '$lib/stores/echo';

export const ECHO_API_BASE_URL = `${WEBUI_BASE_URL}/echo/v1`;

export const getEchoToken = (): string =>
	typeof localStorage !== 'undefined' ? (localStorage.token ?? '') : '';

const buildEchoHeaders = (token: string, body?: BodyInit | null, extra?: HeadersInit): Headers => {
	const headers = new Headers(extra);
	if (!headers.has('Accept')) {
		headers.set('Accept', 'application/json');
	}
	if (!(body instanceof FormData) && !headers.has('Content-Type')) {
		headers.set('Content-Type', 'application/json');
	}
	if (token && !headers.has('authorization')) {
		headers.set('authorization', `Bearer ${token}`);
	}
	return headers;
};

const extractEchoErrorMessage = async (res: Response, defaultError: string): Promise<string> => {
	try {
		const err = await res.json();
		if (err && typeof err === 'object') {
			return err.detail || err.error || err.message || defaultError;
		}
	} catch {
		// Ignorer les réponses non-JSON
	}
	return defaultError;
};

export async function echoRequestJson<T>(
	path: string,
	token: string,
	init: RequestInit = {},
	defaultError = 'Echo API error'
): Promise<T> {
	const res = await fetch(`${ECHO_API_BASE_URL}${path}`, {
		...init,
		headers: buildEchoHeaders(token, init.body, init.headers)
	});

	if (!res.ok) {
		throw new Error(await extractEchoErrorMessage(res, defaultError));
	}
	return res.json();
}

export const echoFetchBlob = async (
	path: string,
	token: string,
	signal?: AbortSignal
): Promise<Blob> => {
	const url = path.startsWith('http://') || path.startsWith('https://') || path.startsWith(ECHO_API_BASE_URL)
		? path
		: `${ECHO_API_BASE_URL}${path}`;
	const res = await fetch(url, {
		method: 'GET',
		headers: token ? { Authorization: `Bearer ${token}` } : undefined,
		signal
	});

	if (!res.ok) {
		throw new Error(await extractEchoErrorMessage(res, `HTTP ${res.status}`));
	}
	return res.blob();
};

export interface EchoSkillItem {
	id: string;
	name: string;
	description: string;
	instructions?: string;
	content?: string;
	user_id?: string;
	is_active?: boolean;
	write_access?: boolean;
}

export interface EchoMetaArtifactItem {
	id: string;
	memory_id: string;
	summary: string;
	artifact_name: string;
	memory_importance: number;
	importance_label?: string;
	tags: string[];
	timestamp: number;
	user_id?: string;
}

export interface EchoMetaArtifactsResponse {
	items: EchoMetaArtifactItem[];
	total: number;
	counts: {
		pacp: number;
		prac: number;
		axioms: number;
	};
}

// ============================================================================
// SKILLS ECHO (/echo/v1/skills)
// ============================================================================

export const listEchoSkills = async (token: string): Promise<EchoSkillItem[]> =>
	echoRequestJson<EchoSkillItem[]>('/skills/', token, { method: 'GET' }, 'Échec de chargement des skills');

export const getEchoSkill = async (token: string, skillId: string): Promise<EchoSkillItem> =>
	echoRequestJson<EchoSkillItem>(
		`/skills/${encodeURIComponent(skillId)}`,
		token,
		{ method: 'GET' },
		'Skill introuvable'
	);

export const saveEchoSkill = async (
	token: string,
	skillId: string,
	payload: { name: string; description: string; instructions: string }
): Promise<EchoSkillItem> =>
	echoRequestJson<EchoSkillItem>(
		`/skills/${encodeURIComponent(skillId)}`,
		token,
		{ method: 'PUT', body: JSON.stringify(payload) },
		'Échec de sauvegarde du skill'
	);

export const deleteEchoSkill = async (
	token: string,
	skillId: string
): Promise<{ deleted: boolean; id: string }> =>
	echoRequestJson<{ deleted: boolean; id: string }>(
		`/skills/${encodeURIComponent(skillId)}`,
		token,
		{ method: 'DELETE' },
		'Échec de suppression du skill'
	);

// ============================================================================
// MÉTA-ARTÉFACTS QDRANT (/echo/v1/meta-artifacts)
// ============================================================================

export const listEchoMetaArtifacts = async (
	token: string,
	params?: { artifact_name?: string; importance?: number; tag?: string; q?: string }
): Promise<EchoMetaArtifactsResponse> => {
	const searchParams = new URLSearchParams();
	if (params?.artifact_name) searchParams.append('artifact_name', params.artifact_name);
	if (params?.importance !== undefined && params?.importance !== null) {
		searchParams.append('importance', params.importance.toString());
	}
	if (params?.tag) searchParams.append('tag', params.tag);
	if (params?.q) searchParams.append('q', params.q);

	return echoRequestJson<EchoMetaArtifactsResponse>(
		`/meta-artifacts/?${searchParams.toString()}`,
		token,
		{ method: 'GET' },
		'Échec de chargement des méta-artéfacts'
	);
};

export const createEchoMetaArtifact = async (
	token: string,
	payload: {
		memory_id?: string;
		summary: string;
		artifact_name: string;
		memory_importance: number;
		tags: string[];
	}
): Promise<EchoMetaArtifactItem> =>
	echoRequestJson<EchoMetaArtifactItem>(
		'/meta-artifacts/',
		token,
		{ method: 'POST', body: JSON.stringify(payload) },
		'Échec de création du méta-artéfact'
	);

export const updateEchoMetaArtifact = async (
	token: string,
	memoryId: string,
	payload: {
		summary?: string;
		artifact_name?: string;
		memory_importance?: number;
		tags?: string[];
	}
): Promise<EchoMetaArtifactItem> =>
	echoRequestJson<EchoMetaArtifactItem>(
		`/meta-artifacts/${encodeURIComponent(memoryId)}`,
		token,
		{ method: 'PATCH', body: JSON.stringify(payload) },
		'Échec de mise à jour du méta-artéfact'
	);

export const promoteEchoMetaArtifact = async (
	token: string,
	memoryId: string
): Promise<{ id: string; memory_id: string; memory_importance: number; importance_label: string }> =>
	echoRequestJson<{ id: string; memory_id: string; memory_importance: number; importance_label: string }>(
		`/meta-artifacts/${encodeURIComponent(memoryId)}/promote`,
		token,
		{ method: 'POST' },
		'Échec de promotion du méta-artéfact'
	);

export const deleteEchoMetaArtifact = async (
	token: string,
	memoryId: string
): Promise<{ deleted: boolean; memory_id: string; id: string }> =>
	echoRequestJson<{ deleted: boolean; memory_id: string; id: string }>(
		`/meta-artifacts/${encodeURIComponent(memoryId)}`,
		token,
		{ method: 'DELETE' },
		'Échec de suppression du méta-artéfact'
	);

// ============================================================================
// WINDOW MANAGER ECHO (/echo/v1/wm)
// ============================================================================

export const getEchoWmChatSnapshot = async (
	token: string,
	chatId: string,
	refresh: boolean = false
): Promise<any> => {
	const targetCid = chatId && chatId.trim() ? chatId.trim() : '_new';
	const query = refresh ? '?refresh=1' : '';
	return echoRequestJson<any>(
		`/wm/chat/${encodeURIComponent(targetCid)}${query}`,
		token,
		{ method: 'GET' },
		'Échec du snapshot Window Manager'
	);
};

export const postEchoWmRpc = async (token: string, payload: Record<string, any>): Promise<any> =>
	echoRequestJson<any>(
		'/wm/rpc',
		token,
		{ method: 'POST', body: JSON.stringify(payload) },
		'Échec appel RPC Window Manager'
	);

// ============================================================================
// COGNITION ECHO (/echo/v1/cognition)
// ============================================================================

export interface EchoBrowserFrame {
	file_id?: string;
	live_frame_id?: string;
	filename?: string;
	session_id?: string;
	url?: string;
	action?: string;
	timestamp?: number;
	image_url?: string;
	b64?: string;
	live_b64?: string;
	webp_b64?: string;
	retained?: boolean;
	status?: string;
}

export const getEchoBrowserFrames = async (
	token: string,
	chatId: string
): Promise<{ frames: EchoBrowserFrame[] }> =>
	echoRequestJson<{ frames: EchoBrowserFrame[] }>(
		`/cognition/${encodeURIComponent(chatId)}/browser-frames`,
		token,
		{ method: 'GET' },
		'Échec de lecture des captures navigateur'
	);

export const getEchoBrowserFrameImageUrl = (chatId: string, fileId: string): string =>
	`${ECHO_API_BASE_URL}/cognition/${encodeURIComponent(chatId)}/browser-frames/${encodeURIComponent(fileId)}/image`;

export const fetchEchoBrowserFrameBlob = async (
	token: string,
	chatId: string,
	fileIdOrUrl: string,
	signal?: AbortSignal
): Promise<Blob> => {
	const target = fileIdOrUrl.startsWith(ECHO_API_BASE_URL)
		? fileIdOrUrl.slice(ECHO_API_BASE_URL.length)
		: fileIdOrUrl.startsWith('/') || fileIdOrUrl.startsWith('http://') || fileIdOrUrl.startsWith('https://')
			? fileIdOrUrl
			: `/cognition/${encodeURIComponent(chatId)}/browser-frames/${encodeURIComponent(fileIdOrUrl)}/image`;
	return echoFetchBlob(target, token, signal);
};

export const getEchoCognitionSnapshot = async (
	token: string,
	chatId: string
): Promise<EchoCognitionSnapshot> =>
	echoRequestJson<EchoCognitionSnapshot>(
		`/cognition/${encodeURIComponent(chatId)}/snapshot`,
		token,
		{ method: 'GET' },
		'Échec du snapshot cognition'
	);

export interface EchoSubagentMessageResponse {
	status: 'queued' | 'started';
	sub_sid: string;
	queue_length?: number;
}

export const postEchoSubagentMessage = async (
	token: string,
	chatId: string,
	subSid: string,
	content: string
): Promise<EchoSubagentMessageResponse> =>
	echoRequestJson<EchoSubagentMessageResponse>(
		`/cognition/${encodeURIComponent(chatId)}/threads/${encodeURIComponent(subSid)}/message`,
		token,
		{ method: 'POST', body: JSON.stringify({ content }) },
		"Échec d'envoi du message au sous-agent"
	);

export type EchoSubagentControlAction = 'suspend' | 'resume' | 'kill' | 'close';

export interface EchoSubagentControlRequest {
	action: EchoSubagentControlAction;
	directive?: string;
	target_model_key?: 'MODEL_ZEPHYR' | 'MODEL_APOLLO' | 'MODEL_PROMETHEUS' | 'MODEL_ATHENA';
}

export interface EchoSubagentControlResponse {
	status: 'ok' | 'started';
	action: EchoSubagentControlAction;
	sub_sid: string;
	affected_sids: string[];
}

export const postEchoSubagentControl = async (
	token: string,
	chatId: string,
	subSid: string,
	payload: EchoSubagentControlRequest
): Promise<EchoSubagentControlResponse> =>
	echoRequestJson<EchoSubagentControlResponse>(
		`/cognition/${encodeURIComponent(chatId)}/threads/${encodeURIComponent(subSid)}/control`,
		token,
		{ method: 'POST', body: JSON.stringify(payload) },
		'Échec du pilotage du sous-agent'
	);

// ============================================================================
// CODEX GIT & FICHIERS (/echo/v1/codex)
// ============================================================================

export interface EchoCodexChatEntry {
	id: string;
	title: string;
}

export interface EchoCodexChatsResponse {
	current: EchoCodexChatEntry;
	others: EchoCodexChatEntry[];
}

export const getEchoCodexChats = async (
	token: string,
	chatId: string
): Promise<EchoCodexChatsResponse> =>
	echoRequestJson<EchoCodexChatsResponse>(
		`/codex/${encodeURIComponent(chatId)}/chats`,
		token,
		{ method: 'GET' },
		'Échec de lecture des conversations Codex'
	);

export const getEchoCodexTree = async (
	token: string,
	chatId: string,
	workspace = 'main',
	path = ''
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path });
	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/tree?${searchParams.toString()}`,
		token,
		{ method: 'GET' },
		'Échec de lecture de l’arborescence Codex'
	);
};

export const getEchoCodexFile = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string,
	commit?: string
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path });
	if (commit) searchParams.append('commit', commit);
	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/file?${searchParams.toString()}`,
		token,
		{ method: 'GET' },
		'Échec de lecture du fichier Codex'
	);
};

export const saveEchoCodexFile = async (
	token: string,
	chatId: string,
	payload: { workspace: string; path: string; content: string; message?: string }
): Promise<any> =>
	echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/file`,
		token,
		{ method: 'PUT', body: JSON.stringify(payload) },
		'Échec d’enregistrement du fichier Codex'
	);

export const deleteEchoCodexFile = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path });
	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/file?${searchParams.toString()}`,
		token,
		{ method: 'DELETE' },
		'Échec de suppression du fichier Codex'
	);
};

export const getEchoCodexLog = async (
	token: string,
	chatId: string,
	workspace = 'main',
	path = '',
	limit = 30
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path, limit: limit.toString() });
	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/log?${searchParams.toString()}`,
		token,
		{ method: 'GET' },
		'Échec de lecture de l’historique Codex'
	);
};

export const getEchoCodexDiff = async (
	token: string,
	chatId: string,
	workspace: string,
	commitA: string,
	commitB?: string
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, commit_a: commitA });
	if (commitB) searchParams.append('commit_b', commitB);
	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/diff?${searchParams.toString()}`,
		token,
		{ method: 'GET' },
		'Échec de calcul du diff Codex'
	);
};

export const getEchoCodexDownloadUrl = (chatId: string, workspace: string, path: string): string => {
	const searchParams = new URLSearchParams({ workspace, path });
	return `${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/download?${searchParams.toString()}`;
};

export const downloadEchoCodexFileBlob = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string,
	signal?: AbortSignal
): Promise<Blob> => {
	const searchParams = new URLSearchParams({ workspace, path });
	return echoFetchBlob(
		`/codex/${encodeURIComponent(chatId)}/download?${searchParams.toString()}`,
		token,
		signal
	);
};

export const mkdirEchoCodex = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string
): Promise<any> =>
	echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/mkdir`,
		token,
		{ method: 'POST', body: JSON.stringify({ workspace, path }) },
		'Échec de création du dossier Codex'
	);

export const renameEchoCodex = async (
	token: string,
	chatId: string,
	workspace: string,
	old_path: string,
	new_path: string
): Promise<any> =>
	echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/rename`,
		token,
		{ method: 'POST', body: JSON.stringify({ workspace, old_path, new_path }) },
		'Échec de renommage Codex'
	);

export const restoreEchoCodexFile = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string,
	commit_hash: string
): Promise<any> =>
	echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/restore`,
		token,
		{ method: 'POST', body: JSON.stringify({ workspace, path, commit_hash }) },
		'Échec de restauration Codex'
	);

export const uploadEchoCodexFile = async (
	token: string,
	chatId: string,
	workspace: string,
	file: File,
	path: string = ''
): Promise<any> => {
	const formData = new FormData();
	formData.append('file', file);
	formData.append('workspace', workspace);
	formData.append('path', path);

	return echoRequestJson<any>(
		`/codex/${encodeURIComponent(chatId)}/upload`,
		token,
		{ method: 'POST', body: formData },
		"Échec de l'importation du fichier Codex"
	);
};

export interface EchoCodexAiEditPayload {
	workspace: string;
	path: string;
	instruction?: string;
	quick_action?:
		| 'shorter'
		| 'longer'
		| 'comment'
		| 'uncomment'
		| 'refactor'
		| 'fix'
		| 'tests'
		| 'optimize';
	content: string;
	selection?: string | null;
	language?: string;
	model?: string;
}

export const aiEditEchoCodexFile = async (
	token: string,
	chatId: string,
	payload: EchoCodexAiEditPayload
): Promise<{
	status: string;
	modified_content: string;
	model_used: string;
	path: string;
	workspace: string;
}> =>
	echoRequestJson<{
		status: string;
		modified_content: string;
		model_used: string;
		path: string;
		workspace: string;
	}>(
		`/codex/${encodeURIComponent(chatId)}/ai-edit`,
		token,
		{ method: 'POST', body: JSON.stringify(payload) },
		"Échec de l'édition IA du fichier Codex"
	);

// ============================================================================
// COFFRE & IDENTITÉ (/echo/v1/vault)
// ============================================================================

export const getEchoVaultStatus = async (token: string): Promise<any> =>
	echoRequestJson<any>(
		'/vault/status',
		token,
		{ method: 'GET' },
		'Échec de lecture du statut Vault'
	);

export const listEchoVaultSecrets = async (token: string): Promise<any> =>
	echoRequestJson<any>(
		'/vault/secrets',
		token,
		{ method: 'GET' },
		'Échec de lecture des secrets Vault'
	);

export const saveEchoVaultSecret = async (
	token: string,
	payload: { service: string; account_id: string; secret: string }
): Promise<any> =>
	echoRequestJson<any>(
		'/vault/secrets',
		token,
		{ method: 'PUT', body: JSON.stringify(payload) },
		'Échec d’enregistrement du secret Vault'
	);

export const deleteEchoVaultSecret = async (
	token: string,
	service: string,
	accountId: string
): Promise<any> =>
	echoRequestJson<any>(
		`/vault/secrets/${encodeURIComponent(service)}/${encodeURIComponent(accountId)}`,
		token,
		{ method: 'DELETE' },
		'Échec de suppression du secret Vault'
	);

export interface EchoVaultSchemaField {
	id: string;
	label: string;
	type?: string;
	required?: boolean;
	help?: string;
	placeholder?: string;
	[key: string]: any;
}

export interface EchoVaultServiceSchema {
	name: string;
	category?: string;
	fields?: EchoVaultSchemaField[];
	user_required_instructions?: string;
	[key: string]: any;
}

export interface EchoVaultSchemasResponse {
	broker_available: boolean;
	schemas: Record<string, EchoVaultServiceSchema>;
}

export const getEchoVaultSchemas = async (token: string): Promise<EchoVaultSchemasResponse> =>
	echoRequestJson<EchoVaultSchemasResponse>(
		'/vault/schemas',
		token,
		{ method: 'GET' },
		'Échec de lecture des schémas de connecteurs'
	);
