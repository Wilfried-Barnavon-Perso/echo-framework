<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getEchoVaultStatus,
		listEchoVaultSecrets,
		saveEchoVaultSecret,
		deleteEchoVaultSecret
	} from '$lib/apis/echo';

	let loading = true;
	let vaultStatus: any = null;
	let secrets: any[] = [];

	let showAddModal = false;
	let newService = '';
	let newAccountId = '';
	let newSecret = '';
	let isSaving = false;
	let isDeleting = false;

	const loadData = async () => {
		const token = localStorage?.token;
		if (!token) return;
		loading = true;
		try {
			const [statusRes, secretsRes] = await Promise.all([
				getEchoVaultStatus(token).catch(() => null),
				listEchoVaultSecrets(token).catch(() => ({ items: [] }))
			]);
			vaultStatus = statusRes;
			secrets = secretsRes?.items || secretsRes || [];
		} catch (e: any) {
			toast.error('Erreur chargement Vault: ' + (e.message || ''));
		} finally {
			loading = false;
		}
	};

	const handleAddSecret = async () => {
		if (!newService.trim() || !newAccountId.trim() || !newSecret.trim()) {
			toast.error('Veuillez remplir tous les champs');
			return;
		}
		const token = localStorage?.token;
		if (!token) return;

		isSaving = true;
		try {
			await saveEchoVaultSecret(token, {
				service: newService.trim(),
				account_id: newAccountId.trim(),
				secret: newSecret.trim()
			});
			toast.success('Secret chiffré AES-256-GCM enregistré dans le Vault');
			showAddModal = false;
			newService = '';
			newAccountId = '';
			newSecret = '';
			loadData();
		} catch (e: any) {
			toast.error('Échec enregistrement secret: ' + (e.message || ''));
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
			toast.success('Secret supprimé du Vault');
			loadData();
		} catch (e: any) {
			toast.error('Échec suppression: ' + (e.message || ''));
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
				Coffre de Secrets & Identité (Vault)
			</span>
			<span class="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-500 font-mono font-medium">
				AES-256-GCM
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
				on:click={() => (showAddModal = true)}
			>
				<span>+ Ajouter un Secret</span>
			</button>
		</div>
	</div>

	{#if loading}
		<div class="flex items-center justify-center h-48">
			<Spinner className="w-6 h-6 text-amber-500" />
		</div>
	{:else}
		<div class="p-4 space-y-6 max-w-4xl">
			<!-- Section 1 : Statut Identités & Quotas Gemini -->
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
				<div class="px-4 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
					<span class="font-bold text-xs text-gray-800 dark:text-gray-200 uppercase tracking-wide">
						Identité Gemini & Quotas Actifs
					</span>
					<span class="text-[11px] font-mono text-gray-500">identity.db -> auth_data</span>
				</div>

				<div class="p-4 space-y-3 text-xs">
					{#if !vaultStatus}
						<div class="text-gray-400">Aucune donnée d'authentification active.</div>
					{:else}
						<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono">
							<div class="p-2.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80">
								<div class="text-gray-400 text-[10px]">FOURNISSEUR ACTIF</div>
								<div class="font-bold text-gray-900 dark:text-white mt-0.5">
									{vaultStatus.provider || 'Google Gemini / Code Assist'}
								</div>
							</div>
							<div class="p-2.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80">
								<div class="text-gray-400 text-[10px]">TIER COGNITIF</div>
								<div class="font-bold text-emerald-500 mt-0.5">
									{vaultStatus.tier || 'Standard'}
								</div>
							</div>
						</div>

						{#if vaultStatus.quotas}
							<div class="mt-2 p-3 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80 font-mono space-y-2">
								<div class="font-bold text-gray-700 dark:text-gray-300">Consommation des Quotas :</div>
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
										<span class="text-gray-400">RPD : </span>
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

			<!-- Section 2 : Secrets Chiffrés (identity_vault) -->
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
				<div class="px-4 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
					<span class="font-bold text-xs text-gray-800 dark:text-gray-200 uppercase tracking-wide">
						Identifiants & Secrets Chiffrés (AES-256-GCM)
					</span>
					<span class="text-[11px] font-mono text-gray-500">{secrets.length} entrée(s)</span>
				</div>

				<div class="p-4">
					{#if secrets.length === 0}
						<div class="text-center py-6 text-xs text-gray-400">
							Aucun secret stocké dans le coffre souverain.
						</div>
					{:else}
						<div class="space-y-2">
							{#each secrets as s}
								<div class="flex items-center justify-between p-3 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/80 dark:border-gray-800/80 font-mono text-xs">
									<div class="flex items-center gap-3">
										<div class="w-2 h-2 rounded-full bg-amber-500"></div>
										<div>
											<span class="font-bold text-gray-900 dark:text-white">{s.service}</span>
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

	<!-- Modal Ajout Secret -->
	{#if showAddModal}
		<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-150">
			<div class="w-full max-w-md bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl shadow-2xl p-5 space-y-4 text-xs">
				<div class="flex justify-between items-center pb-2 border-b border-gray-200 dark:border-gray-800">
					<h3 class="font-bold text-sm text-gray-900 dark:text-white">Nouveau Secret Chiffré</h3>
					<button
						type="button"
						class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
						on:click={() => (showAddModal = false)}
					>
						✕
					</button>
				</div>

				<div class="space-y-3 font-mono">
					<div>
						<label class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">Service / Fournisseur :</label>
						<input
							type="text"
							class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-amber-500"
							placeholder="ex: github, aws, gemini_api"
							bind:value={newService}
						/>
					</div>

					<div>
						<label class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">Identifiant / Compte :</label>
						<input
							type="text"
							class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-amber-500"
							placeholder="ex: user@domain.com ou key_prod_1"
							bind:value={newAccountId}
						/>
					</div>

					<div>
						<label class="block text-gray-600 dark:text-gray-400 mb-1 font-semibold">Secret / Token (Chiffré AES-GCM) :</label>
						<input
							type="password"
							class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-amber-500"
							placeholder="••••••••••••••••"
							bind:value={newSecret}
						/>
					</div>
				</div>

				<div class="flex justify-end gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
					<button
						type="button"
						class="px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 font-medium transition"
						on:click={() => (showAddModal = false)}
					>
						Annuler
					</button>
					<button
						type="button"
						class="px-4 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-600 text-white font-medium shadow-sm transition"
						on:click={handleAddSecret}
						disabled={isSaving}
					>
						{isSaving ? 'Chiffrement...' : 'Enregistrer dans le Coffre'}
					</button>
				</div>
			</div>
		</div>
	{/if}
</div>
