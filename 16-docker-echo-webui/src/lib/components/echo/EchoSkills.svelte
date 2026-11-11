<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import { workspaceCounts } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		listEchoSkills,
		getEchoSkill,
		saveEchoSkill,
		deleteEchoSkill,
		type EchoSkillItem
	} from '$lib/apis/echo';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let skills: EchoSkillItem[] = [];
	let loading = true;
	let query = '';

	// Modale Édition / Création
	let showModal = false;
	let modalMode: 'create' | 'edit' = 'create';
	let formId = '';
	let formName = '';
	let formDesc = '';
	let formInstructions = '';
	let isSaving = false;
	let isDeleting = false;

	const loadSkills = async () => {
		loading = true;
		try {
			skills = await listEchoSkills(localStorage.token || '');
			workspaceCounts.update((counts) => ({
				...counts,
				skills: skills.length
			}));
		} catch (err: any) {
			toast.error(err.message || 'Impossible de charger les skills ECHO');
		} finally {
			loading = false;
		}
	};

	const openCreateModal = () => {
		modalMode = 'create';
		formId = '';
		formName = '';
		formDesc = '';
		formInstructions = '# Protocole PAS (Instructions Métier)\n\nQuand cette compétence est requise, le modèle DOIT :\n1. \n';
		showModal = true;
	};

	const openEditModal = async (skill: EchoSkillItem) => {
		try {
			const detailed = await getEchoSkill(localStorage.token || '', skill.id);
			modalMode = 'edit';
			formId = detailed.id;
			formName = detailed.name;
			formDesc = detailed.description;
			formInstructions = detailed.instructions || '';
			showModal = true;
		} catch (err: any) {
			toast.error(err.message || 'Impossible de charger le détail du skill');
		}
	};

	const closeModal = () => {
		if (isSaving) return;
		showModal = false;
	};

	const handleSave = async () => {
		const targetId = formId.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
		if (!targetId) {
			toast.error("L'identifiant du skill est obligatoire.");
			return;
		}
		if (!formName.trim()) {
			toast.error('Le nom du skill est obligatoire.');
			return;
		}

		isSaving = true;
		try {
			await saveEchoSkill(localStorage.token || '', targetId, {
				name: formName.trim(),
				description: formDesc.trim(),
				instructions: formInstructions
			});
			toast.success(
				modalMode === 'create'
					? `Compétence "${formName}" créée avec succès.`
					: `Compétence "${formName}" mise à jour.`
			);
			showModal = false;
			await loadSkills();
		} catch (err: any) {
			toast.error(err.message || 'Erreur lors de la sauvegarde du skill');
		} finally {
			isSaving = false;
		}
	};

	const handleDelete = async (skill: EchoSkillItem) => {
		const confirmed = window.confirm(
			`Êtes-vous certain de vouloir supprimer définitivement le skill "${skill.name}" (${skill.id}) ?\nCette opération effacera le fichier SKILL.md sur disque.`
		);
		if (!confirmed) return;

		isDeleting = true;
		try {
			await deleteEchoSkill(localStorage.token || '', skill.id);
			toast.success(`Compétence "${skill.name}" supprimée.`);
			await loadSkills();
		} catch (err: any) {
			toast.error(err.message || 'Erreur lors de la suppression');
		} finally {
			isDeleting = false;
		}
	};

	$: filteredSkills = skills.filter((s) => {
		if (!query.trim()) return true;
		const q = query.toLowerCase();
		return (
			s.name.toLowerCase().includes(q) ||
			s.id.toLowerCase().includes(q) ||
			s.description.toLowerCase().includes(q)
		);
	});

	onMount(() => {
		loadSkills();
	});
</script>

<div class="flex flex-col w-full h-full p-4 md:p-6 space-y-6">
	<!-- En-tête Workspace Skills ECHO -->
	<div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
		<div>
			<div class="flex items-center gap-2">
				<h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
					Skills Souverains ECHO
				</h1>
				<span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
					Kernel PAS
				</span>
			</div>
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
				Compétences et règles procédurales injectées dynamiquement aux sous-agents et à la Table Ronde Delphi.
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
				Nouveau Skill
			</button>
		</div>
	</div>

	<!-- Barre de recherche -->
	<div class="relative w-full max-w-md">
		<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
			</svg>
		</div>
		<input
			type="text"
			bind:value={query}
			placeholder="Rechercher par identifiant, nom ou description..."
			class="w-full pl-9 pr-4 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
		/>
	</div>

	<!-- Contenu : Liste ou État de Chargement -->
	{#if loading}
		<div class="flex flex-col items-center justify-center flex-1 py-16">
			<Spinner className="size-8 text-blue-500" />
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-3">Chargement des compétences ECHO...</p>
		</div>
	{:else if filteredSkills.length === 0}
		<div class="flex flex-col items-center justify-center flex-1 py-16 border-2 border-dashed border-gray-200 dark:border-gray-800 rounded-2xl text-center p-6">
			<div class="p-3 bg-gray-100 dark:bg-gray-850 rounded-2xl text-gray-400 mb-3">
				<svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
				</svg>
			</div>
			<h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">Aucun Skill trouvé</h3>
			<p class="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-sm">
				{query ? 'Aucun résultat ne correspond à votre recherche.' : 'Créez votre première compétence procédurale pour spécialiser votre système.'}
			</p>
			{#if !query}
				<button
					type="button"
					class="mt-4 px-4 py-2 text-sm font-medium rounded-xl text-blue-600 bg-blue-50 hover:bg-blue-100 dark:bg-blue-500/10 dark:hover:bg-blue-500/20 dark:text-blue-400 transition"
					on:click={openCreateModal}
				>
					Créer un Skill
				</button>
			{/if}
		</div>
	{:else}
		<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
			{#each filteredSkills as skill (skill.id)}
				<div class="flex flex-col justify-between p-5 rounded-2xl bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 shadow-sm hover:border-gray-300 dark:hover:border-gray-700 transition">
					<div class="space-y-2">
						<div class="flex items-start justify-between gap-2">
							<h3 class="font-semibold text-gray-900 dark:text-gray-100 text-base line-clamp-1">
								{skill.name}
							</h3>
							<span class="inline-flex px-2 py-0.5 text-xs font-mono rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700">
								{skill.id}
							</span>
						</div>
						<p class="text-sm text-gray-500 dark:text-gray-400 line-clamp-2">
							{skill.description || 'Aucune description fournie.'}
						</p>
					</div>

					<div class="flex items-center justify-end gap-2 pt-4 mt-4 border-t border-gray-100 dark:border-gray-800">
						<button
							type="button"
							class="px-3 py-1.5 text-xs font-medium rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
							on:click={() => openEditModal(skill)}
						>
							Éditer
						</button>
						<button
							type="button"
							class="px-3 py-1.5 text-xs font-medium rounded-lg text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10 transition"
							on:click={() => handleDelete(skill)}
						>
							Supprimer
						</button>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<!-- Modale d'Édition / Création de Skill -->
{#if showModal}
	<div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
		<div class="flex flex-col w-full max-w-2xl max-h-[90vh] bg-white dark:bg-gray-900 rounded-2xl shadow-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
			<!-- Header Modale -->
			<div class="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
				<h3 class="text-lg font-bold text-gray-900 dark:text-gray-100">
					{modalMode === 'create' ? 'Nouveau Skill ECHO' : `Édition : ${formName}`}
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

			<!-- Corps Modale -->
			<div class="flex-1 p-6 space-y-4 overflow-y-auto">
				<div>
					<label for="skill-id-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Identifiant unique (slug dossier)
					</label>
					<input
						id="skill-id-input"
						type="text"
						bind:value={formId}
						disabled={modalMode === 'edit'}
						placeholder="ex: analyse_financiere"
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					/>
				</div>

				<div>
					<label for="skill-name-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Nom affiché
					</label>
					<input
						id="skill-name-input"
						type="text"
						bind:value={formName}
						placeholder="ex: Analyse Financière Approfondie"
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					/>
				</div>

				<div>
					<label for="skill-desc-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Description (déclencheur pour routage sous-agents & Table Ronde Delphi)
					</label>
					<textarea
						id="skill-desc-input"
						bind:value={formDesc}
						rows="2"
						placeholder="Spécifie dans quel cadre et pour quelles tâches ce skill doit être activé..."
						class="w-full px-3 py-2 text-sm rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					></textarea>
				</div>

				<div>
					<label for="skill-instructions-input" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
						Instructions Métier (Markdown / Protocole PAS)
					</label>
					<textarea
						id="skill-instructions-input"
						bind:value={formInstructions}
						rows="10"
						placeholder="Règles procédurales, contraintes et protocoles stricts..."
						class="w-full font-mono text-xs px-3 py-2 rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
					></textarea>
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
						Enregistrement...
					{:else}
						Enregistrer
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}
