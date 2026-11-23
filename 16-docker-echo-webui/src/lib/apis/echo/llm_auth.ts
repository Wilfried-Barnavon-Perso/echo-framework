import { echoRequestJson } from '$lib/apis/echo';

export type ApiKeySlot = 'primary' | 'secondary';

export interface PkceFlowLastResult {
	success: boolean;
	message: string;
	finished_at: number;
}

export interface PkceFlowState {
	active: boolean;
	ssh_command: string;
	temp_password: string;
	auth_url: string;
	server_ip: string;
	ssh_port: number;
	cb_port: number;
	expires_in_sec: number;
	ssh_tunnel_connected: boolean;
	last_result: PkceFlowLastResult | null;
}

export interface LlmQuotaSnapshot {
	auth_sources: string[];
	user_email: string | null;
	user_tier: string | null;
	project_id: string | null;
	tenant_id: string;
	credits_val: string;
	quota_fraction: number;
	quota_reset: string;
	quota_type: string;
	quota_model: string;
	quota_rpd_rem: string;
	quota_rpd_lim: string;
	quota_rpm_rem: string;
	quota_rpm_lim: string;
	quota_5h_fraction: number | null;
	quota_5h_reset: string;
	quota_5h_disabled: boolean;
	quota_weekly_fraction: number | null;
	quota_weekly_reset: string;
}

export interface LlmAuthStatusResponse {
	user_id: string;
	oauth_connected: boolean;
	snapshot: LlmQuotaSnapshot;
	api_keys: {
		primary_configured: boolean;
		primary_masked: string;
		secondary_configured: boolean;
		secondary_masked: string;
	};
	pkce_flow: PkceFlowState;
}

export interface OAuthStartResponse {
	status: string;
	pkce_flow: PkceFlowState;
}

export const getLlmAuthStatus = async (
	token: string,
	refresh = false
): Promise<LlmAuthStatusResponse> => {
	const query = refresh ? '?refresh=true' : '';
	return echoRequestJson<LlmAuthStatusResponse>(
		`/llm-auth/status${query}`,
		token,
		{ method: 'GET' },
		'Impossible de récupérer le statut d’authentification LLM'
	);
};

export const startLlmOAuthFlow = async (token: string): Promise<OAuthStartResponse> =>
	echoRequestJson<OAuthStartResponse>(
		'/llm-auth/oauth/start',
		token,
		{ method: 'POST' },
		'Impossible de démarrer la connexion OAuth2 Google'
	);

export const cancelLlmOAuthFlow = async (token: string): Promise<{ status: string }> =>
	echoRequestJson<{ status: string }>(
		'/llm-auth/oauth/cancel',
		token,
		{ method: 'POST' },
		'Impossible d’annuler la connexion OAuth2'
	);

export const saveLlmApiKey = async (
	token: string,
	apiKey: string,
	slot?: ApiKeySlot
): Promise<{ status: string; slot: ApiKeySlot; masked_key: string }> =>
	echoRequestJson(
		'/llm-auth/api-key',
		token,
		{
			method: 'PUT',
			body: JSON.stringify(slot ? { api_key: apiKey, slot } : { api_key: apiKey })
		},
		'Impossible de valider ou d’enregistrer la clé API'
	);

export const deleteLlmApiKey = async (
	token: string,
	slot: ApiKeySlot
): Promise<{ status: string; slot: ApiKeySlot }> =>
	echoRequestJson(
		`/llm-auth/api-key/${encodeURIComponent(slot)}`,
		token,
		{ method: 'DELETE' },
		'Impossible de supprimer la clé API'
	);

export const resetLlmAuth = async (
	token: string
): Promise<{ status: string; deleted: number }> =>
	echoRequestJson(
		'/llm-auth/reset',
		token,
		{ method: 'DELETE' },
		'Impossible de réinitialiser l’authentification LLM'
	);
