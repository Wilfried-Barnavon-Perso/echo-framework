<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getEchoVaultStatus,
		getEchoVaultSchemas,
		listEchoVaultSecrets,
		saveEchoVaultSecret,
		deleteEchoVaultSecret,
		type EchoVaultServiceSchema
	} from '$lib/apis/echo';
	import { echoVaultTarget } from '$lib/stores/echo';

	let loading = true;
	let vaultStatus: any = null;
	let secrets: any[] = [];
	let brokerAvailable = true;
	let schemas: Record<string, EchoVaultServiceSchema> = {};

	let showAddModal = false;
	let selectedSchemaKey: string | null = null;
	let newService = '';
	let newAccountId = '';
	let newSecret = '';
	let dynamicValues: Record<string, string> = {};
	let revealedFields: Record<string, boolean> = {};
	let isSaving = false;
	let isDeleting = false;

	const VAULT_FIELD_CLASS =
		'w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-white font-mono focus:outline-none focus:ring-1 focus:ring-amber-500';

	const N8N_FALLBACK_SCHEMA: EchoVaultServiceSchema = {
		name: 'Automatisations n8n',
		category: 'orchestration',
		fields: [{ id: 'api_key', label: 'Clé API n8n', type: 'password', required: true }]
	};

	const CATEGORY_LABELS: Record<string, string> = {
		mcp_builtin: 'MCP intégré',
		builtin: 'MCP intégré',
		mcp: 'MCP intégré',
		resident: 'Service résident',
		resident_service: 'Service résident',
		orchestration: 'Orchestration',
		remote: 'Serveur distant',
		remote_mcp: 'Serveur distant',
		remote_server: 'Serveur distant'
	};

	const translateCategory = (category?: string): string => {
		if (!category) return 'Service externe';
		const normalized = category.trim().toLowerCase();
		return CATEGORY_LABELS[normalized] || category;
	};

	const getAccountCount = (serviceKey: string): number =>
		secrets.filter((s) => s.service === serviceKey).length;

	const loadData = async () => {
		const token = localStorage?.token;
		if (!token) return;
		loading = true;
		try {
			const [statusRes, secretsRes, schemasRes] = await Promise.all([
				getEchoVaultStatus(token).catch(() => null),
				listEchoVaultSecrets(token).catch(() => ({ items: [] })),
				getEchoVaultSchemas(token).catch(() => ({ broker_available: false, schemas: {} }))
			]);
			vaultStatus = statusRes;
			secrets = secretsRes?.items || secretsRes || [];
			brokerAvailable = schemasRes?.broker_available ?? false;
			const rawSchemas = schemasRes?.schemas ?? {};
			schemas = {
				...rawSchemas,
				...(rawSchemas.n8n_workflows ? {} : { n8n_workflows: N8N_FALLBACK_SCHEMA })
			};
		} catch (e: any) {
			toast.error('Erreur chargement du coffre : ' + (e.message || ''));
		} finally {
			loading = false;
			if ($echoVaultTarget) {
				consumeVaultTarget($echoVaultTarget);
			}
		}
	};

	const consumeVaultTarget = (target: { openSchema?: string; ts?: number } | null) => {
		if (!target || loading) return;
		echoVaultTarget.set(null);
		if (target.openSchema && (target.openSchema === 'ssh' || target.openSchema in schemas)) {
			openSchemaModal(target.openSchema);
		}
	};

	$: if (!loading && $echoVaultTarget) {
		consumeVaultTarget($echoVaultTarget);
	}

	const openSchemaModal = (serviceKey: string) => {
		selectedSchemaKey = serviceKey;
		newService = serviceKey;
		newAccountId = getAccountCount(serviceKey) === 0 ? 'default' : '';
		newSecret = '';
		const fields = schemas[serviceKey]?.fields ?? [];
		const nextValues: Record<string, string> = {};
		for (const f of fields) {
			nextValues[f.id] = '';
		}
		dynamicValues = nextValues;
		revealedFields = {};
		showAddModal = true;
	};

	const openCustomModal = () => {
		selectedSchemaKey = null;
		newService = '';
		newAccountId = '';
		newSecret = '';
		dynamicValues = {};
		revealedFields = {};
		showAddModal = true;
	};

	const closeAddModal = () => {
		showAddModal = false;
		revealedFields = {};
	};

	$: activeSchema = selectedSchemaKey ? schemas[selectedSchemaKey] ?? null : null;
	$: activeSchemaFields = activeSchema?.fields ?? [];
	$: isDynamicSchemaMode = Boolean(selectedSchemaKey && activeSchemaFields.length > 0);

	const handleAddSecret = async () => {
		const targetService = (selectedSchemaKey || newService).trim();
		const targetAccount = newAccountId.trim();

		if (!targetService || !targetAccount) {
			toast.error('Veuillez renseigner le service et l’identifiant de compte.');
			return;
		}

		let secretPayload = '';
		if (isDynamicSchemaMode) {
			const collected: Record<string, string> = {};
			for (const field of activeSchemaFields) {
				const val = (dynamicValues[field.id] ?? '').trim();
				if (field.required && !val) {
					toast.error(`Le champ « ${field.label} » est obligatoire.`);
					return;
				}
				if (val) {
					collected[field.id] = val;
				}
			}
			if (Object.keys(collected).length === 0) {
				toast.error('Veuillez remplir au moins un champ.');
				return;
			}
			secretPayload = JSON.stringify(collected);
		} else {
			if (!newSecret.trim()) {
				toast.error('Veuillez renseigner le secret.');
				return;
			}
			secretPayload = newSecret.trim();
		}

		const token = localStorage?.token;
		if (!token) return;

		isSaving = true;
		try {
			await saveEchoVaultSecret(token, {
				service: targetService,
				account_id: targetAccount,
				secret: secretPayload
			});
			toast.success('Identifiant enregistré dans le coffre sécurisé.');
			closeAddModal();
			selectedSchemaKey = null;
			newService = '';
			newAccountId = '';
			newSecret = '';
			dynamicValues = {};
			await loadData();
		} catch (e: any) {
			toast.error('Échec de l’enregistrement : ' + (e.message || ''));
		} finally {
			isSaving = false;
		}
	};

	const handleDeleteSecret = async (service: string, accountId: string) => {
		if (!confirm(`Supprimer définitivement le secret pour [${service}] (${accountId}) ?`)) return;
		const token = localStorage?.token;
		if (!token) return;

		isDeleting = true;
		try {
			await deleteEchoVaultSecret(token, service, accountId);
			toast.success('Secret supprimé du coffre.');
			await loadData();
		} catch (e: any) {
			toast.error('Échec de la suppression : ' + (e.message || ''));
		} finally {
			isDeleting = false;
		}
	};

	onMount(() => {
		loadData();
	});
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-y-auto">
	<!-- Barre de titre -->
	<div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850">
		<div class="flex items-center gap-2">
			<span class="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-1.5">
				<svg class="w-4 h-4 text-amber-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
					<path d="M7 11V7a5 5 0 0 1 10 0v4" />
				</svg>
				Coffre &amp; Connecteurs
			</span>
		</div>

		<div class="flex items-center gap-2">
			<button
				type="button"
				class="px-2.5 py-1 text-xs rounded-lg bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700 font-medium transition"
				on:click={loadData}
				title="Actualiser"
			>
				Actualiser
			</button>
			<button
				type="button"
				class="px-3 py-1 text-xs rounded-lg bg-amber-500 hover:bg-amber-600 text-white font-medium shadow-sm transition flex items-center gap-1"
				on:click={openCustomModal}
			>
				<span>+ Secret personnalisé</span>
			</button>
		</div>
	</div>

	{#if loading}
		<div class="flex items-center justify-center h-48">
			<Spinner className="w-6 h-6 text-amber-500" />
		</div>
	{:else}
		<div class="p-4 space-y-6 max-w-4xl">
			<!-- Section 1 : Statut Compte & Quotas -->
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
				<div class="px-4 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
					<span class="font-bold text-xs text-gray-800 dark:text-gray-200 uppercase tracking-wide">
						Compte &amp; Quotas Actifs
					</span>
				</div>

				<div class="p-4 space-y-3 text-xs">
					{#if !vaultStatus}
						<div class="text-gray-400">Aucune donnée de compte active.</div>
					{:else}
						<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono">
							<div class="p-2.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80">
								<div class="text-gray-400 text-[10px]">FOURNISSEUR ACTIF</div>
								<div class="font-bold text-gray-900 dark:text-white mt-0.5">
									{vaultStatus.provider || 'Google Gemini'}
								</div>
							</div>
							<div class="p-2.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80">
								<div class="text-gray-400 text-[10px]">NIVEAU D'ACCÈS</div>
								<div class="font-bold text-emerald-500 mt-0.5">
									{vaultStatus.tier || 'Standard'}
								</div>
							</div>
						</div>

						{#if vaultStatus.quotas}
							<div class="mt-2 p-3 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80 font-mono space-y-2">
								<div class="font-bold text-gray-700 dark:text-gray-300">Quotas du compte :</div>
								<div class="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
									<div>
										<span class="text-gray-400">Fenêtre 5h : </span>
										<span class="font-semibold text-gray-800 dark:text-gray-200">
											{vaultStatus.quotas.five_hour_pct ?? '0'}%
										</span>
									</div>
									<div>
										<span class="text-gray-400">Hebdomadaire : </span>
										<span class="font-semibold text-gray-800 dark:text-gray-200">
											{vaultStatus.quotas.weekly_pct ?? '0'}%
										</span>
									</div>
									<div>
										<span class="text-gray-400">Requêtes / jour : </span>
										<span class="font-semibold text-gray-800 dark:text-gray-200">
											{vaultStatus.quotas.rpd_usage ?? '0'}
										</span>
									</div>
								</div>
							</div>
						{/if}
					{/if}
				</div>
			</div>

			<!-- Section 2 : Connecteurs & Outils Externes -->
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
				<div class="px-4 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
					<span class="font-bold text-xs text-gray-800 dark:text-gray-200 uppercase tracking-wide">
						Connecteurs &amp; Outils Externes
					</span>
					<span class="text-[11px] font-mono text-gray-500">{Object.keys(schemas).length} service(s)</span>
				</div>

				<div class="p-4 space-y-3">
					{#if !brokerAvailable}
						<div class="px-3 py-2 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200/80 dark:border-amber-800/50 text-amber-800 dark:text-amber-300 text-xs">
							Le catalogue complet des connecteurs externes est momentanément indisponible. Les connecteurs locaux et l'ajout personnalisé restent accessibles.
						</div>
					{/if}

					<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
						{#each Object.entries(schemas) as [serviceKey, schema] (serviceKey)}
							{@const configuredCount = secrets.filter((s) => s.service === serviceKey).length}
							<div class="flex items-center justify-between gap-3 p-3 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80 text-xs">
								<div class="min-w-0 space-y-1">
									<div class="font-semibold text-gray-900 dark:text-white truncate">
										{schema.name || serviceKey}
									</div>
									<div class="flex flex-wrap items-center gap-1.5">
										<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
											{translateCategory(schema.category)}
										</span>
										<span class="text-[10px] px-1.5 py-0.5 rounded {configuredCount > 0
											? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
											: 'bg-gray-100 dark:bg-gray-800 text-gray-400'}">
											{configuredCount} compte{configuredCount > 1 ? 's' : ''}
										</span>
									</div>
								</div>

								<button
									type="button"
									class="px-2.5 py-1.5 rounded-lg text-xs font-medium shrink-0 transition {configuredCount > 0
										? 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200'
										: 'bg-amber-500 hover:bg-amber-600 text-white shadow-sm'}"
									on:click={() => openSchemaModal(serviceKey)}
								>
									{configuredCount > 0 ? '+ Ajouter un compte' : 'Configurer'}
								</button>
							</div>
						{/each}
					</div>
				</div>
			</div>

			<!-- Section 3 : Secrets enregistrés -->
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
				<div class="px-4 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
					<span class="font-bold text-xs text-gray-800 dark:text-gray-200 uppercase tracking-wide">
						Identifiants &amp; Secrets enregistrés
					</span>
					<span class="text-[11px] font-mono text-gray-500">{secrets.length} entrée(s)</span>
				</div>

				<div class="p-4">
					{#if secrets.length === 0}
						<div class="text-center py-6 text-xs text-gray-400">
							Aucun secret stocké dans le coffre sécurisé.
						</div>
					{:else}
						<div class="space-y-2">
							{#each secrets as s}
								<div class="flex items-center justify-between p-3 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80 font-mono text-xs">
									<div class="flex items-center gap-3">
										<div class="w-2 h-2 rounded-full bg-amber-500"></div>
										<div>
											<span class="font-bold text-gray-900 dark:text-white">
												{schemas[s.service]?.name || s.service}
											</span>
											<span class="text-gray-400 text-[11px] ml-2">({s.account_id})</span>
										</div>
									</div>

									<div class="flex items-center gap-3">
										<span class="text-gray-400 text-xs">••••••••••••••••</span>
										<button
											type="button"
											class="text-red-500 hover:text-red-700 text-xs font-semibold px-2 py-1 rounded bg-red-500/10 hover:bg-red-500/20 transition"
											on:click={() => handleDeleteSecret(s.service, s.account_id)}
											disabled={isDeleting}
										>
											Supprimer
										</button>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}

	<!-- Modale Ajout Secret / Configuration Connecteur -->
	{#if showAddModal}
		<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-150">
			<div class="w-full {activeSchema?.user_required_instructions ? 'max-w-lg' : 'max-w-md'} max-h-[90vh] overflow-y-auto bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-2xl p-5 space-y-4 text-xs">
				<div class="flex justify-between items-center pb-2 border-b border-gray-200 dark:border-gray-800">
					<h3 class="font-bold text-sm text-gray-900 dark:text-white">
						{activeSchema ? `Configurer ${activeSchema.name || selectedSchemaKey}` : 'Nouveau secret personnalisé'}
					</h3>
					<button
						type="button"
						class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
						on:click={closeAddModal}
					>
						✕
					</button>
				</div>

				{#if activeSchema?.user_required_instructions}
					<details class="rounded-lg border border-amber-300/70 dark:border-amber-700/60 bg-amber-50/70 dark:bg-amber-950/30 p-3 text-amber-900 dark:text-amber-200">
						<summary class="cursor-pointer font-semibold select-none">
							Prérequis &amp; Directives d'autorisation (API / Scopes OAuth)
						</summary>
						<pre class="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-gray-800 dark:text-gray-200 max-h-56 overflow-y-auto">{activeSchema.user_required_instructions}</pre>
					</details>
				{/if}

				<form class="space-y-3" autocomplete="off" on:submit|preventDefault={handleAddSecret}>
					{#if !selectedSchemaKey}
						<div>
							<label for="vault-service-input" class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">
								Service / Fournisseur :
							</label>
							<input
								id="vault-service-input"
								type="text"
								autocomplete="off"
								spellcheck="false"
								data-lpignore="true"
								data-1p-ignore
								class={VAULT_FIELD_CLASS}
								placeholder="ex: github, aws, notion"
								bind:value={newService}
							/>
						</div>
					{/if}

					<div>
						<label for="vault-account-input" class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">
							Identifiant du compte :
						</label>
						<input
							id="vault-account-input"
							type="text"
							autocomplete="off"
							spellcheck="false"
							data-lpignore="true"
							data-1p-ignore
							class={VAULT_FIELD_CLASS}
							placeholder={selectedSchemaKey === 'ssh'
								? 'ex: vps-prod, nas-local'
								: selectedSchemaKey
									? 'ex: default, pro, perso'
									: 'ex: user@domain.com ou principal'}
							bind:value={newAccountId}
						/>
					</div>

					{#if isDynamicSchemaMode}
						{#each activeSchemaFields as field (field.id)}
							<div>
								<label for={`vault-field-${field.id}`} class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">
									{field.label}{#if field.required}<span class="text-red-500 ml-0.5">*</span>{/if}
								</label>
								{#if field.type === 'textarea_secret'}
									<div class="space-y-1">
										<div class="flex justify-end">
											<button
												type="button"
												class="text-[11px] text-amber-600 dark:text-amber-400 hover:underline"
												on:click={() => (revealedFields[field.id] = !revealedFields[field.id])}
											>
												{revealedFields[field.id] ? 'Masquer' : 'Afficher pour vérifier'}
											</button>
										</div>
										<textarea
											id={`vault-field-${field.id}`}
											rows="4"
											autocomplete="off"
											spellcheck="false"
											data-lpignore="true"
											data-1p-ignore
											class="{VAULT_FIELD_CLASS} text-xs {revealedFields[field.id] ? '' : '[-webkit-text-security:disc]'}"
											placeholder={field.placeholder || '-----BEGIN OPENSSH PRIVATE KEY-----'}
											bind:value={dynamicValues[field.id]}
										></textarea>
									</div>
								{:else if field.type === 'textarea'}
									<textarea
										id={`vault-field-${field.id}`}
										rows="3"
										autocomplete="off"
										spellcheck="false"
										data-lpignore="true"
										data-1p-ignore
										class="{VAULT_FIELD_CLASS} text-xs"
										placeholder={field.placeholder || ''}
										bind:value={dynamicValues[field.id]}
									></textarea>
								{:else if field.type === 'password'}
									<input
										id={`vault-field-${field.id}`}
										type="password"
										autocomplete="new-password"
										data-lpignore="true"
										data-1p-ignore
										class={VAULT_FIELD_CLASS}
										placeholder={field.placeholder || '••••••••••••••••'}
										bind:value={dynamicValues[field.id]}
									/>
								{:else}
									<input
										id={`vault-field-${field.id}`}
										type="text"
										autocomplete="off"
										spellcheck="false"
										data-lpignore="true"
										data-1p-ignore
										class={VAULT_FIELD_CLASS}
										placeholder={field.placeholder || ''}
										bind:value={dynamicValues[field.id]}
									/>
								{/if}
								{#if field.help}
									<p class="mt-1 text-[11px] text-gray-400">{field.help}</p>
								{/if}
							</div>
						{/each}
					{:else}
						<div>
							<label for="vault-secret-input" class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">
								Secret / Clé d'accès :
							</label>
							<input
								id="vault-secret-input"
								type="password"
								autocomplete="new-password"
								data-lpignore="true"
								data-1p-ignore
								class={VAULT_FIELD_CLASS}
								placeholder="••••••••••••••••"
								bind:value={newSecret}
							/>
						</div>
					{/if}
				</form>

				<div class="flex justify-end gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
					<button
						type="button"
						class="px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 font-medium transition"
						on:click={closeAddModal}
					>
						Annuler
					</button>
					<button
						type="button"
						class="px-4 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-600 text-white font-medium shadow-sm transition disabled:opacity-50"
						on:click={handleAddSecret}
						disabled={isSaving}
					>
						{isSaving ? 'Enregistrement...' : 'Enregistrer dans le coffre'}
					</button>
				</div>
			</div>
		</div>
	{/if}
</div>
