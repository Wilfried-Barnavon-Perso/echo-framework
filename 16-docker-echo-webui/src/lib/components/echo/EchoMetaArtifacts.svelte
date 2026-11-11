<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		listEchoMetaArtifacts,
		createEchoMetaArtifact,
		updateEchoMetaArtifact,
		promoteEchoMetaArtifact,
		deleteEchoMetaArtifact,
		type EchoMetaArtifactItem,
		type EchoMetaArtifactsResponse
	} from '$lib/apis/echo';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let items: EchoMetaArtifactItem[] = [];
	let loading = true;
	let counts = { pacp: 0, prac: 0, axioms: 0 };

	// Filtres
	let activeTab: 'all' | 'pacp' | 'prac' | 'axioms' = 'all';
	let selectedImportance: number | null = null;
	let query = '';
	let selectedTag = '';

	// Modale Édition / Création
	let showModal = false;
	let modalMode: 'create' | 'edit' = 'create';
	let formMemoryId = '';
	let formArtifactName = "Profil d'Alignement";
	let formSummary = '';
	let formImportance = 3;
	let formTagsInput = '';
	let isSaving = false;

	const IMPORTANCE_LABELS: Record<number, { label: string; color: string; bg: string }> = {
		1: { label: '1 - Trivial', color: 'text-gray-500', bg: 'bg-gray-100 dark:bg-gray-800' },
		2: { label: '2 - Mineur', color: 'text-blue-500', bg: 'bg-blue-50 dark:bg-blue-950/40' },
		3: { label: '3 - Utile', color: 'text-emerald-500', bg: 'bg-emerald-50 dark:bg-emerald-950/40' },
		4: { label: '4 - Majeur', color: 'text-purple-500', bg: 'bg-purple-50 dark:bg-purple-950/40' },
		5: { label: '5 - Axiome', color: 'text-amber-500 dark:text-amber-400 font-bold', bg: 'bg-amber-50 dark:bg-amber-950/50 border border-amber-300 dark:border-amber-700/60' }
	};

	const loadArtifacts = async () => {
		loading = true;
		try {
			let artName: string | undefined = undefined;
			if (activeTab === 'pacp') artName = "Profil d'Alignement";
			else if (activeTab === 'prac') artName = "Hypothèses d'Apprentissage";

			let impFilter = selectedImportance;
			if (activeTab === 'axioms') impFilter = 5;

			const res: EchoMetaArtifactsResponse = await listEchoMetaArtifacts(localStorage.token || '', {
				artifact_name: artName,
				importance: impFilter !== null ? impFilter : undefined,
				tag: selectedTag.trim() || undefined,
				q: query.trim() || undefined
			});
			items = res.items || [];
			counts = res.counts || { pacp: 0, prac: 0, axioms: 0 };
		} catch (err: any) {
			toast.error(err.message || 'Impossible de charger les méta-artéfacts');
		} finally {
			loading = false;
		}
	};

	const openCreateModal = () => {
		modalMode = 'create';
		formMemoryId = '';
		formArtifactName = activeTab === 'prac' ? "Hypothèses d'Apprentissage" : "Profil d'Alignement";
		formSummary = '';
		formImportance = 3;
		formTagsInput = '';
		showModal = true;
	};

	const openEditModal = (item: EchoMetaArtifactItem) => {
		modalMode = 'edit';
		formMemoryId = item.memory_id;
		formArtifactName = item.artifact_name;
		formSummary = item.summary;
		formImportance = item.memory_importance;
		formTagsInput = (item.tags || []).join(', ');
		showModal = true;
	};

	const closeModal = () => {
		if (isSaving) return;
		showModal = false;
	};

	const handleSave = async () => {
		if (!formSummary.trim()) {
			toast.error('Le résumé du méta-artéfact est obligatoire.');
			return;
		}

		isSaving = true;
		const parsedTags = formTagsInput
			.split(',')
			.map((t) => t.trim())
			.filter((t) => t.length > 0);

		try {
			if (modalMode === 'create') {
				await createEchoMetaArtifact(localStorage.token || '', {
					memory_id: formMemoryId.trim() || undefined,
					summary: formSummary.trim(),
					artifact_name: formArtifactName,
					memory_importance: formImportance,
					tags: parsedTags
				});
				toast.success('Méta-artéfact créé et vectorisé avec succès.');
			} else {
				await updateEchoMetaArtifact(localStorage.token || '', formMemoryId, {
					summary: formSummary.trim(),
					artifact_name: formArtifactName,
					memory_importance: formImportance,
					tags: parsedTags
				});
				toast.success('Méta-artéfact mis à jour.');
			}
			showModal = false;
			await loadArtifacts();
		} catch (err: any) {
			toast.error(err.message || "Erreur lors de l'enregistrement");
		} finally {
			isSaving = false;
		}
	};

	const handlePromote = async (item: EchoMetaArtifactItem) => {
		try {
			await promoteEchoMetaArtifact(localStorage.token || '', item.memory_id);
			toast.success(`Méta-artéfact "${item.memory_id}" promu au rang maximal (5 - Axiome).`);
			await loadArtifacts();
		} catch (err: any) {
			toast.error(err.message || 'Erreur lors de la promotion en axiome');
		}
	};

	const handleDelete = async (item: EchoMetaArtifactItem) => {
		const confirmed = window.confirm(
			`Êtes-vous certain de vouloir supprimer le souvenir "${item.memory_id}" ?\nIl sera définitivement purgé de la collection vectorielle Qdrant.`
		);
		if (!confirmed) return;

		try {
			await deleteEchoMetaArtifact(localStorage.token || '', item.memory_id);
			toast.success('Méta-artéfact supprimé de Qdrant.');
			await loadArtifacts();
		} catch (err: any) {
			toast.error(err.message || 'Erreur lors de la suppression');
		}
	};

	const setTab = (tab: 'all' | 'pacp' | 'prac' | 'axioms') => {
		activeTab = tab;
		loadArtifacts();
	};

	const formatTimestamp = (ts: number) => {
		if (!ts) return 'Date inconnue';
		try {
			const d = new Date(ts * 1000);
			return d.toLocaleString('fr-FR', {
				day: '2-digit',
				month: '2-digit',
				year: 'numeric',
				hour: '2-digit',
				minute: '2-digit'
			});
		} catch {
			return String(ts);
		}
	};

	onMount(() => {
		loadArtifacts();
	});
</script>

<div class="flex flex-col w-full h-full p-4 md:p-6 space-y-6">
	<!-- En-tête Méta-Artéfacts Qdrant -->
	<div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
		<div>
			<div class="flex items-center gap-2">
				<h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
					Méta-Artéfacts Mémoriels
				</h1>
				<span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20">
					Qdrant Vector DB
				</span>
			</div>
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
				Alignement PACP (Préférences) et Apprentissage PRAC (Hypothèses) indexés pour l'injection Smart Context.
			</p>
		</div>

		<div class="flex items-center gap-3">
			<button
				type="button"
				class="inline-flex items-center px-4 py-2 text-sm font-medium rounded-xl text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 transition shadow-sm"
				on:click={openCreateModal}
			>
				<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
				</svg>
				Nouveau Souvenir
			</button>
		</div>
	</div>

	<!-- Onglets de Navigation PACP / PRAC / Axiomes / Tous -->
	<div class="flex flex-wrap items-center gap-2 border-b border-gray-200 dark:border-gray-800 pb-3">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium rounded-xl transition {activeTab === 'all'
				? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
				: 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}"
			on:click={() => setTab('all')}
		>
			Vue Unifiée
		</button>
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-sm font-medium rounded-xl transition {activeTab === 'pacp'
				? 'bg-blue-600 text-white'
				: 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}"
			on:click={() => setTab('pacp')}
		>
			<span>Profil d'Alignement (PACP)</span>
			<span class="px-1.5 py-0.2 text-xs rounded-full bg-white/20 dark:bg-white/10">{counts.pacp}</span>
		</button>
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-sm font-medium rounded-xl transition {activeTab === 'prac'
				? 'bg-purple-600 text-white'
				: 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'}"
			on:click={() => setTab('prac')}
		>
			<span>Hypothèses d'Apprentissage (PRAC)</span>
			<span class="px-1.5 py-0.2 text-xs rounded-full bg-white/20 dark:bg-white/10">{counts.prac}</span>
		</button>
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-sm font-medium rounded-xl transition {activeTab === 'axioms'
				? 'bg-amber-600 text-white font-bold'
				: 'text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/30'}"
			on:click={() => setTab('axioms')}
		>
			<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
				<path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
			</svg>
			<span>Axiomes (Niveau 5)</span>
			<span class="px-1.5 py-0.2 text-xs rounded-full bg-black/10 dark:bg-white/10">{counts.axioms}</span>
		</button>
	</div>

	<!-- Barre de Recherche et Filtres -->
	<div class="flex flex-col md:flex-row items-center gap-3">
		<div class="relative flex-1 w-full">
			<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
			</div>
			<input
				type="text"
				bind:value={query}
				on:keydown={(e) => e.key === 'Enter' && loadArtifacts()}
				placeholder="Rechercher dans les souvenirs (mot-clé, tag, identifiant)..."
				class="w-full pl-9 pr-4 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
			/>
		</div>

		<!-- Filtre par Importance -->
		<select
			bind:value={selectedImportance}
			on:change={loadArtifacts}
			disabled={activeTab === 'axioms'}
			class="px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
		>
			<option value={null}>Toutes les importances</option>
			<option value={1}>1 - Trivial</option>
			<option value={2}>2 - Mineur</option>
			<option value={3}>3 - Utile</option>
			<option value={4}>4 - Majeur</option>
			<option value={5}>5 - Axiome</option>
		</select>

		<button
			type="button"
			class="px-4 py-2 text-sm font-medium rounded-xl text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
			on:click={loadArtifacts}
		>
			Filtrer
		</button>
	</div>

	<!-- Contenu : Liste ou Chargement -->
	{#if loading}
		<div class="flex flex-col items-center justify-center flex-1 py-16">
			<Spinner className="size-8 text-purple-500" />
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-3">Interrogation de Qdrant...</p>
		</div>
	{:else if items.length === 0}
		<div class="flex flex-col items-center justify-center flex-1 py-16 border-2 border-dashed border-gray-200 dark:border-gray-800 rounded-2xl text-center p-6">
			<div class="p-3 bg-gray-100 dark:bg-gray-850 rounded-2xl text-gray-400 mb-3">
				<svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
				</svg>
			</div>
			<h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">Aucun Méta-Artéfact trouvé</h3>
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-sm">
				{query || selectedImportance ? 'Aucun souvenir ne correspond aux critères de filtre.' : 'Aucun fait mémoriel n’est encore enregistré dans cette vue.'}
			</p>
			{#if !query}
				<button
					type="button"
					class="mt-4 px-4 py-2 text-sm font-medium rounded-xl text-blue-600 bg-blue-50 hover:bg-blue-100 dark:bg-blue-500/10 dark:hover:bg-blue-500/20 dark:text-blue-400 transition"
					on:click={openCreateModal}
				>
					Créer un Souvenir
				</button>
			{/if}
		</div>
	{:else}
		<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
			{#each items as item (item.id || item.memory_id)}
				<div class="flex flex-col justify-between p-5 rounded-2xl bg-white dark:bg-gray-850 border shadow-sm transition {item.memory_importance === 5 ? 'border-amber-400/60 dark:border-amber-600/60 shadow-amber-500/5' : 'border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'}">
					<div class="space-y-3">
						<!-- En-tête Carte -->
						<div class="flex items-start justify-between gap-2">
							<span class="inline-flex px-2 py-0.5 text-xs font-mono rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 line-clamp-1">
								{item.memory_id}
							</span>
							<span class="inline-flex px-2 py-0.5 text-xs rounded-full {IMPORTANCE_LABELS[item.memory_importance]?.bg} {IMPORTANCE_LABELS[item.memory_importance]?.color}">
								{IMPORTANCE_LABELS[item.memory_importance]?.label || item.memory_importance}
							</span>
						</div>

						<!-- Badge Artéfact Name -->
						<div class="text-xs font-semibold {item.artifact_name === 'Profil d\'Alignement' ? 'text-blue-500' : 'text-purple-500'}">
							{item.artifact_name}
						</div>

						<!-- Résumé du Fait -->
						<p class="text-sm text-gray-800 dark:text-gray-200 font-normal leading-relaxed">
							{item.summary}
						</p>

						<!-- Tags -->
						{#if item.tags && item.tags.length > 0}
							<div class="flex flex-wrap gap-1 pt-1">
								{#each item.tags as tag}
									<span class="px-1.5 py-0.5 text-[10px] font-medium rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
										#{tag}
									</span>
								{/each}
							</div>
						{/if}
					</div>

					<!-- Horodatage & Actions -->
					<div class="flex items-center justify-between pt-4 mt-4 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-400">
						<span>{formatTimestamp(item.timestamp)}</span>

						<div class="flex items-center gap-1.5">
							{#if item.memory_importance < 5}
								<button
									type="button"
									title="Promouvoir immédiatement au niveau 5 (Axiome)"
									class="px-2 py-1 text-xs font-medium rounded-lg text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-950/40 transition"
									on:click={() => handlePromote(item)}
								>
									★ Axiome
								</button>
							{/if}
							<button
								type="button"
								class="px-2 py-1 text-xs font-medium rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
								on:click={() => openEditModal(item)}
							>
								Éditer
							</button>
							<button
								type="button"
								class="px-2 py-1 text-xs font-medium rounded-lg text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10 transition"
								on:click={() => handleDelete(item)}
							>
								Supprimer
							</button>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<!-- Modale Création / Édition -->
{#if showModal}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
		<div class="flex flex-col w-full max-w-lg bg-white dark:bg-gray-900 rounded-2xl shadow-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
			<!-- Header Modale -->
			<div class="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
				<h3 class="text-lg font-bold text-gray-900 dark:text-gray-100">
					{modalMode === 'create' ? 'Nouveau Méta-Artéfact' : `Édition : ${formMemoryId}`}
				</h3>
				<button
					type="button"
					class="p-1 rounded-lg text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
					on:click={closeModal}
				>
					<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- Formulaire Modale -->
			<div class="p-6 space-y-4">
				<div>
					<label for="meta-id-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Identifiant unique (slug)
					</label>
					<input
						id="meta-id-input"
						type="text"
						bind:value={formMemoryId}
						disabled={modalMode === 'edit'}
						placeholder="ex: pref_concision_code"
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					/>
				</div>

				<div>
					<label for="meta-type-select" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Type de Méta-Artéfact
					</label>
					<select
						id="meta-type-select"
						bind:value={formArtifactName}
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					>
						<option value="Profil d'Alignement">Profil d'Alignement (PACP - Préférences)</option>
						<option value="Hypothèses d'Apprentissage">Hypothèses d'Apprentissage (PRAC - Axiomes & Déductions)</option>
					</select>
				</div>

				<div>
					<label for="meta-imp-select" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Niveau d'Importance Cognitive
					</label>
					<select
						id="meta-imp-select"
						bind:value={formImportance}
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					>
						<option value={1}>1 - Trivial (1 occurrence isolée)</option>
						<option value={2}>2 - Mineur (2 occurrences concordantes)</option>
						<option value={3}>3 - Utile (3 occurrences ou pertinence avérée)</option>
						<option value={4}>4 - Majeur (Règle forte)</option>
						<option value={5}>5 - Axiome (Invariant absolu / Injonction explicite)</option>
					</select>
				</div>

				<div>
					<label for="meta-summary-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Résumé du Fait ou de la Règle (Sera vectorisé)
					</label>
					<textarea
						id="meta-summary-input"
						bind:value={formSummary}
						rows="4"
						placeholder="Décrivez précisément le fait, la préférence ou l'axiome..."
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					></textarea>
				</div>

				<div>
					<label for="meta-tags-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Tags (séparés par des virgules)
					</label>
					<input
						id="meta-tags-input"
						type="text"
						bind:value={formTagsInput}
						placeholder="ex: style, concision, architecture"
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					/>
				</div>
			</div>

			<!-- Footer Modale -->
			<div class="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50">
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium rounded-xl text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={closeModal}
					disabled={isSaving}
				>
					Annuler
				</button>
				<button
					type="button"
					class="inline-flex items-center px-4 py-2 text-sm font-medium rounded-xl text-white bg-blue-600 hover:bg-blue-700 active:bg-blue-800 transition shadow-sm disabled:opacity-50"
					on:click={handleSave}
					disabled={isSaving}
				>
					{#if isSaving}
						<Spinner className="size-4 mr-2" />
						Vectorisation...
					{:else}
						Enregistrer
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
