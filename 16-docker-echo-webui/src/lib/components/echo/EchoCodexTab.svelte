<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getEchoCodexTree,
		getEchoCodexFile,
		saveEchoCodexFile,
		deleteEchoCodexFile,
		getEchoCodexLog,
		getEchoCodexDiff,
		getEchoCodexDownloadUrl
	} from '$lib/apis/echo';

	export let chatId: string = '';

	let currentChatId = '';
	let workspaces = [
		{ id: 'main', label: 'Main', writable: true },
		{ id: 'sandbox', label: 'Sandbox', writable: true },
		{ id: 'plans', label: 'Plans', writable: false },
		{ id: 'generated', label: 'Generated', writable: false },
		{ id: 'files', label: 'Fichiers', writable: false }
	];

	let selectedWorkspace = 'main';
	let selectedPath = '';
	let fileContent = '';
	let commitMessage = '';
	let currentCommit = '';
	let activeSubView: 'editor' | 'log' | 'diff' = 'editor';

	let treeEntries: any[] = [];
	let logEntries: any[] = [];
	let diffText = '';

	let loadingTree = false;
	let loadingFile = false;
	let isSaving = false;
	let isDeleting = false;

	$: currentWsObj = workspaces.find((w) => w.id === selectedWorkspace) || workspaces[0];
	$: isWritable = currentWsObj.writable;

	$: if (chatId && chatId !== currentChatId) {
		currentChatId = chatId;
		loadTree();
	}

	const loadTree = async () => {
		const token = localStorage?.token;
		if (!token || !chatId) return;
		loadingTree = true;
		try {
			const res = await getEchoCodexTree(token, chatId, selectedWorkspace, '');
			treeEntries = res?.entries || res || [];
		} catch (e: any) {
			toast.error('Erreur de chargement Codex: ' + (e.message || ''));
		} finally {
			loadingTree = false;
		}
	};

	const selectFile = async (path: string) => {
		selectedPath = path;
		activeSubView = 'editor';
		const token = localStorage?.token;
		if (!token || !chatId) return;

		loadingFile = true;
		try {
			const res = await getEchoCodexFile(token, chatId, selectedWorkspace, path);
			fileContent = typeof res === 'string' ? res : res?.content ?? JSON.stringify(res, null, 2);
		} catch (e: any) {
			toast.error('Erreur lecture fichier: ' + (e.message || ''));
		} finally {
			loadingFile = false;
		}
	};

	const handleSave = async () => {
		if (!isWritable || !selectedPath) return;
		const token = localStorage?.token;
		if (!token || !chatId) return;

		isSaving = true;
		try {
			await saveEchoCodexFile(token, chatId, {
				workspace: selectedWorkspace,
				path: selectedPath,
				content: fileContent,
				message: commitMessage.trim() || `Update ${selectedPath} via Codex UI`
			});
			toast.success('Fichier enregistré et commité avec succès');
			commitMessage = '';
			loadTree();
		} catch (e: any) {
			toast.error('Échec enregistrement: ' + (e.message || ''));
		} finally {
			isSaving = false;
		}
	};

	const handleDelete = async () => {
		if (!isWritable || !selectedPath) return;
		if (!confirm(`Confirmer la suppression de ${selectedPath} ?`)) return;
		const token = localStorage?.token;
		if (!token || !chatId) return;

		isDeleting = true;
		try {
			await deleteEchoCodexFile(token, chatId, selectedWorkspace, selectedPath);
			toast.success('Fichier supprimé');
			selectedPath = '';
			fileContent = '';
			loadTree();
		} catch (e: any) {
			toast.error('Échec suppression: ' + (e.message || ''));
		} finally {
			isDeleting = false;
		}
	};

	const loadLog = async () => {
		activeSubView = 'log';
		const token = localStorage?.token;
		if (!token || !chatId) return;
		try {
			const res = await getEchoCodexLog(token, chatId, selectedWorkspace, selectedPath, 30);
			logEntries = res?.commits || res || [];
		} catch (e: any) {
			toast.error('Erreur historique: ' + (e.message || ''));
		}
	};

	const loadDiff = async (commitHash: string) => {
		activeSubView = 'diff';
		currentCommit = commitHash;
		const token = localStorage?.token;
		if (!token || !chatId) return;
		try {
			const res = await getEchoCodexDiff(token, chatId, selectedWorkspace, commitHash);
			diffText = typeof res === 'string' ? res : res?.diff || '';
		} catch (e: any) {
			toast.error('Erreur diff: ' + (e.message || ''));
		}
	};

	onMount(() => {
		if (chatId) loadTree();
	});
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre de contrôle supérieure -->
	<div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850">
		<div class="flex items-center gap-2">
			<span class="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-1.5">
				<svg class="w-4 h-4 text-sky-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M16.5 9.4 7.55 4.24a1.78 1.78 0 0 0-2.5 1.55v12.42a1.78 1.78 0 0 0 2.5 1.55L16.5 14.6a1.78 1.78 0 0 0 0-3.2z" />
				</svg>
				Codex Git
			</span>

			<!-- Sélecteur de Workspace -->
			<select
				class="text-xs bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-2 py-1 font-semibold text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
				bind:value={selectedWorkspace}
				on:change={() => {
					selectedPath = '';
					fileContent = '';
					loadTree();
				}}
			>
				{#each workspaces as ws}
					<option value={ws.id}>{ws.label} {ws.writable ? '[RW]' : '[RO]'}</option>
				{/each}
			</select>

			<span
				class="text-[10px] px-1.5 py-0.5 rounded font-mono font-bold {isWritable
					? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
					: 'bg-gray-200 dark:bg-gray-700 text-gray-500'}"
			>
				{isWritable ? 'LECTURE-ÉCRITURE' : 'LECTURE SEULE'}
			</span>
		</div>

		<!-- Boutons sous-vues -->
		<div class="flex items-center gap-1 bg-gray-200 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium">
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeSubView === 'editor' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-400'}"
				on:click={() => (activeSubView = 'editor')}
			>
				Éditeur
			</button>
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeSubView === 'log' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-400'}"
				on:click={loadLog}
			>
				Historique Git
			</button>
			{#if activeSubView === 'diff'}
				<button
					type="button"
					class="px-2.5 py-1 rounded-md bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white font-mono text-[11px]"
				>
					Diff ({currentCommit.slice(0, 7)})
				</button>
			{/if}
		</div>
	</div>

	<!-- Corps splité : Arborescence à gauche, contenu au centre -->
	<div class="flex flex-1 overflow-hidden">
		<!-- Explorateur arborescent (gauche) -->
		<div class="w-60 border-r border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-950/40 flex flex-col">
			<div class="p-2 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center text-xs font-semibold text-gray-600 dark:text-gray-400">
				<span>Fichiers</span>
				<button
					type="button"
					class="p-1 hover:text-sky-500"
					on:click={loadTree}
					title="Rafraîchir l'arbre"
				>
					<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
					</svg>
				</button>
			</div>

			<div class="flex-1 overflow-y-auto p-1 space-y-0.5">
				{#if loadingTree}
					<div class="p-4 flex justify-center">
						<Spinner className="w-5 h-5 text-sky-500" />
					</div>
				{:else if treeEntries.length === 0}
					<div class="p-4 text-center text-xs text-gray-400">Aucun fichier dans ce workspace.</div>
				{:else}
					{#each treeEntries as entry}
						{@const p = typeof entry === 'string' ? entry : entry.path || entry.name}
						<button
							type="button"
							class="w-full text-left px-2 py-1.5 rounded-md text-xs font-mono truncate transition flex items-center gap-1.5 {selectedPath === p ? 'bg-sky-500/10 text-sky-600 dark:text-sky-400 font-semibold' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'}"
							on:click={() => selectFile(p)}
							title={p}
						>
							<svg class="w-3.5 h-3.5 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
								<polyline points="14 2 14 8 20 8" />
							</svg>
							<span class="truncate">{p}</span>
						</button>
					{/each}
				{/if}
			</div>
		</div>

		<!-- Zone Principale (droite) -->
		<div class="flex-1 flex flex-col overflow-hidden bg-white dark:bg-gray-900">
			{#if activeSubView === 'editor'}
				{#if !selectedPath}
					<div class="flex flex-col items-center justify-center h-full text-gray-400 text-xs">
						<svg class="w-12 h-12 mb-2 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
						</svg>
						Sélectionnez un fichier dans l'arborescence à gauche.
					</div>
				{:else if loadingFile}
					<div class="flex items-center justify-center h-full">
						<Spinner className="w-6 h-6 text-sky-500" />
					</div>
				{:else}
					<!-- Barre d'actions fichier -->
					<div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50">
						<span class="font-mono text-xs font-semibold text-gray-800 dark:text-gray-200 truncate">
							{selectedPath}
						</span>

						<div class="flex items-center gap-2">
							<a
								href={getEchoCodexDownloadUrl(chatId, selectedWorkspace, selectedPath)}
								download
								class="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-xs font-medium text-gray-700 dark:text-gray-300 transition"
								title="Télécharger"
							>
								Télécharger
							</a>

							{#if isWritable}
								<button
									type="button"
									class="px-2 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-red-500 text-xs font-medium transition"
									on:click={handleDelete}
									disabled={isDeleting}
								>
									{isDeleting ? '...' : 'Supprimer'}
								</button>
								<button
									type="button"
									class="px-3 py-1 rounded bg-sky-500 hover:bg-sky-600 text-white text-xs font-medium shadow-sm transition"
									on:click={handleSave}
									disabled={isSaving}
								>
									{isSaving ? 'Enregistrement...' : 'Enregistrer (Commit)'}
								</button>
							{/if}
						</div>
					</div>

					{#if isWritable}
						<div class="px-4 py-1.5 border-b border-gray-100 dark:border-gray-800 bg-gray-50/20">
							<input
								type="text"
								class="w-full text-xs font-mono bg-transparent border-none focus:outline-none text-gray-600 dark:text-gray-400 placeholder-gray-400"
								placeholder="Message de commit optionnel (ex: 'Update {selectedPath}')"
								bind:value={commitMessage}
							/>
						</div>
					{/if}

					<!-- Zone d'édition / visualisation -->
					<div class="flex-1 p-2">
						<textarea
							class="w-full h-full p-3 font-mono text-xs bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-sky-500 resize-none"
							bind:value={fileContent}
							readonly={!isWritable}
						></textarea>
					</div>
				{/if}
			{:else if activeSubView === 'log'}
				<div class="flex-1 overflow-y-auto p-4 space-y-2">
					<h3 class="text-xs font-bold text-gray-700 dark:text-gray-300 mb-2">Historique Git Dulwich</h3>
					{#if logEntries.length === 0}
						<div class="text-gray-400 text-xs">Aucun commit trouvé pour ce chemin.</div>
					{:else}
						{#each logEntries as c}
							<div class="p-3 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-850/50 font-mono text-xs space-y-1">
								<div class="flex justify-between items-center">
									<button
										type="button"
										class="text-sky-500 font-bold hover:underline"
										on:click={() => loadDiff(c.hash || c.commit_id || c.sha)}
									>
										{(c.hash || c.commit_id || c.sha || '').slice(0, 8)}
									</button>
									<span class="text-[11px] text-gray-400">{c.author || ''}</span>
								</div>
								<div class="text-gray-800 dark:text-gray-200 font-sans font-medium">{c.message}</div>
								{#if c.timestamp}
									<div class="text-[10px] text-gray-400">{new Date(c.timestamp * 1000).toLocaleString()}</div>
								{/if}
							</div>
						{/each}
					{/if}
				</div>
			{:else if activeSubView === 'diff'}
				<div class="flex-1 flex flex-col p-4 overflow-hidden">
					<div class="flex justify-between items-center mb-2">
						<span class="text-xs font-bold text-gray-700 dark:text-gray-300">Diff Commit {currentCommit}</span>
						<button
							type="button"
							class="text-xs text-sky-500 hover:underline"
							on:click={() => (activeSubView = 'log')}
						>
							Retour aux logs
						</button>
					</div>
					<pre class="flex-1 overflow-auto p-3 font-mono text-xs bg-gray-950 text-emerald-400 rounded-xl border border-gray-800">{diffText || 'Aucune différence ou diff vide.'}</pre>
				</div>
			{/if}
		</div>
	</div>
</div>
