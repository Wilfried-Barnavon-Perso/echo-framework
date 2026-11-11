import { WEBUI_BASE_URL } from '$lib/constants';

export const ECHO_API_BASE_URL = `${WEBUI_BASE_URL}/echo/v1`;

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

export const listEchoSkills = async (token: string): Promise<EchoSkillItem[]> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/skills/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de chargement des skills' }));
		throw new Error(err.detail || 'Échec de chargement des skills');
	}
	return res.json();
};

export const getEchoSkill = async (token: string, skillId: string): Promise<EchoSkillItem> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/skills/${encodeURIComponent(skillId)}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Skill introuvable' }));
		throw new Error(err.detail || 'Skill introuvable');
	}
	return res.json();
};

export const saveEchoSkill = async (
	token: string,
	skillId: string,
	payload: { name: string; description: string; instructions: string }
): Promise<EchoSkillItem> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/skills/${encodeURIComponent(skillId)}`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de sauvegarde du skill' }));
		throw new Error(err.detail || 'Échec de sauvegarde du skill');
	}
	return res.json();
};

export const deleteEchoSkill = async (token: string, skillId: string): Promise<{ deleted: boolean; id: string }> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/skills/${encodeURIComponent(skillId)}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de suppression du skill' }));
		throw new Error(err.detail || 'Échec de suppression du skill');
	}
	return res.json();
};

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

	const url = `${ECHO_API_BASE_URL}/meta-artifacts/?${searchParams.toString()}`;
	const res = await fetch(url, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de chargement des méta-artéfacts' }));
		throw new Error(err.detail || 'Échec de chargement des méta-artéfacts');
	}
	return res.json();
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
): Promise<EchoMetaArtifactItem> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/meta-artifacts/`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de création du méta-artéfact' }));
		throw new Error(err.detail || 'Échec de création du méta-artéfact');
	}
	return res.json();
};

export const updateEchoMetaArtifact = async (
	token: string,
	memoryId: string,
	payload: {
		summary?: string;
		artifact_name?: string;
		memory_importance?: number;
		tags?: string[];
	}
): Promise<EchoMetaArtifactItem> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/meta-artifacts/${encodeURIComponent(memoryId)}`, {
		method: 'PATCH',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de mise à jour du méta-artéfact' }));
		throw new Error(err.detail || 'Échec de mise à jour du méta-artéfact');
	}
	return res.json();
};

export const promoteEchoMetaArtifact = async (
	token: string,
	memoryId: string
): Promise<{ id: string; memory_id: string; memory_importance: number; importance_label: string }> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/meta-artifacts/${encodeURIComponent(memoryId)}/promote`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de promotion du méta-artéfact' }));
		throw new Error(err.detail || 'Échec de promotion du méta-artéfact');
	}
	return res.json();
};

export const deleteEchoMetaArtifact = async (
	token: string,
	memoryId: string
): Promise<{ deleted: boolean; memory_id: string; id: string }> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/meta-artifacts/${encodeURIComponent(memoryId)}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de suppression du méta-artéfact' }));
		throw new Error(err.detail || 'Échec de suppression du méta-artéfact');
	}
	return res.json();
};

// ============================================================================
// WINDOW MANAGER ECHO (/echo/v1/wm)
// ============================================================================

export const getEchoWmChatSnapshot = async (token: string, chatId: string): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/wm/chat/${encodeURIComponent(chatId)}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec du snapshot Window Manager' }));
		throw new Error(err.detail || 'Échec du snapshot Window Manager');
	}
	return res.json();
};

export const postEchoWmRpc = async (token: string, payload: Record<string, any>): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/wm/rpc`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec appel RPC Window Manager' }));
		throw new Error(err.detail || 'Échec appel RPC Window Manager');
	}
	return res.json();
};

// ============================================================================
// COGNITION ECHO (/echo/v1/cognition)
// ============================================================================

export const getEchoCognitionSnapshot = async (token: string, chatId: string): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/cognition/${encodeURIComponent(chatId)}/snapshot`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec du snapshot cognition' }));
		throw new Error(err.detail || 'Échec du snapshot cognition');
	}
	return res.json();
};

// ============================================================================
// CODEX GIT & FICHIERS (/echo/v1/codex)
// ============================================================================

export const getEchoCodexTree = async (
	token: string,
	chatId: string,
	workspace = 'main',
	path = ''
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path });
	const res = await fetch(
		`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/tree?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de lecture de l’arborescence Codex' }));
		throw new Error(err.detail || 'Échec de lecture de l’arborescence Codex');
	}
	return res.json();
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
	const res = await fetch(
		`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/file?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de lecture du fichier Codex' }));
		throw new Error(err.detail || 'Échec de lecture du fichier Codex');
	}
	return res.json();
};

export const saveEchoCodexFile = async (
	token: string,
	chatId: string,
	payload: { workspace: string; path: string; content: string; message?: string }
): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/file`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec d’enregistrement du fichier Codex' }));
		throw new Error(err.detail || 'Échec d’enregistrement du fichier Codex');
	}
	return res.json();
};

export const deleteEchoCodexFile = async (
	token: string,
	chatId: string,
	workspace: string,
	path: string
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path });
	const res = await fetch(
		`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/file?${searchParams.toString()}`,
		{
			method: 'DELETE',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de suppression du fichier Codex' }));
		throw new Error(err.detail || 'Échec de suppression du fichier Codex');
	}
	return res.json();
};

export const getEchoCodexLog = async (
	token: string,
	chatId: string,
	workspace = 'main',
	path = '',
	limit = 30
): Promise<any> => {
	const searchParams = new URLSearchParams({ workspace, path, limit: limit.toString() });
	const res = await fetch(
		`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/log?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de lecture de l’historique Codex' }));
		throw new Error(err.detail || 'Échec de lecture de l’historique Codex');
	}
	return res.json();
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
	const res = await fetch(
		`${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/diff?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de calcul du diff Codex' }));
		throw new Error(err.detail || 'Échec de calcul du diff Codex');
	}
	return res.json();
};

export const getEchoCodexDownloadUrl = (chatId: string, workspace: string, path: string): string => {
	const searchParams = new URLSearchParams({ workspace, path });
	return `${ECHO_API_BASE_URL}/codex/${encodeURIComponent(chatId)}/download?${searchParams.toString()}`;
};

// ============================================================================
// COFFRE & IDENTITÉ (/echo/v1/vault)
// ============================================================================

export const getEchoVaultStatus = async (token: string): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/vault/status`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de lecture du statut Vault' }));
		throw new Error(err.detail || 'Échec de lecture du statut Vault');
	}
	return res.json();
};

export const listEchoVaultSecrets = async (token: string): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/vault/secrets`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de lecture des secrets Vault' }));
		throw new Error(err.detail || 'Échec de lecture des secrets Vault');
	}
	return res.json();
};

export const saveEchoVaultSecret = async (
	token: string,
	payload: { service: string; account_id: string; secret: string }
): Promise<any> => {
	const res = await fetch(`${ECHO_API_BASE_URL}/vault/secrets`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec d’enregistrement du secret Vault' }));
		throw new Error(err.detail || 'Échec d’enregistrement du secret Vault');
	}
	return res.json();
};

export const deleteEchoVaultSecret = async (
	token: string,
	service: string,
	accountId: string
): Promise<any> => {
	const res = await fetch(
		`${ECHO_API_BASE_URL}/vault/secrets/${encodeURIComponent(service)}/${encodeURIComponent(accountId)}`,
		{
			method: 'DELETE',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: 'Échec de suppression du secret Vault' }));
		throw new Error(err.detail || 'Échec de suppression du secret Vault');
	}
	return res.json();
};
