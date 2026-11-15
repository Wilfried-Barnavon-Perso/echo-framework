<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import {
		getEchoCodexTree,
		getEchoCodexFile,
		saveEchoCodexFile,
		deleteEchoCodexFile,
		getEchoCodexLog,
		getEchoCodexDiff,
		getEchoCodexDownloadUrl,
		mkdirEchoCodex,
		renameEchoCodex,
		restoreEchoCodexFile
	} from '$lib/apis/echo';
	import { echoCodexTarget } from '$lib/stores/echo';

	export let chatId: string = '';

	let currentChatId = '';
	let crossReadChatId = '';
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
	let fileMeta: {
		is_binary?: boolean;
		mime?: string;
		size_bytes?: number;
		hex_preview?: string;
	} | null = null;
	let viewMode: 'code' | 'preview' = 'code';
	let previewBlobUrl = '';

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
	let isMutating = false;

	$: effectiveChatId = crossReadChatId || chatId;
	$: isCrossRead = Boolean(crossReadChatId && crossReadChatId !== chatId);
	$: currentWsObj = workspaces.find((w) => w.id === selectedWorkspace) || workspaces[0];
	$: isWritable = currentWsObj.writable && !isCrossRead;

	const revokePreviewBlob = () => {
		if (previewBlobUrl) {
			try {
				URL.revokeObjectURL(previewBlobUrl);
			} catch {
				// ignore
			}
			previewBlobUrl = '';
		}
	};

	onDestroy(() => {
		revokePreviewBlob();
	});

	$: if (chatId && chatId !== currentChatId) {
		currentChatId = chatId;
		crossReadChatId = '';
		selectedPath = '';
		fileContent = '';
		fileMeta = null;
		revokePreviewBlob();
		if ($echoCodexTarget?.chatId !== chatId) {
			void loadTree();
		}
	}

	const getExt = (path: string): string => {
		const idx = path.lastIndexOf('.');
		return idx >= 0 ? path.slice(idx + 1).toLowerCase() : '';
	};

	$: fileExt = getExt(selectedPath);
	$: isImageFile =
		Boolean(fileMeta?.mime?.startsWith('image/')) ||
		['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico'].includes(fileExt);
	$: isMarkdownFile = ['md', 'markdown'].includes(fileExt);
	$: isSvgFile = fileExt === 'svg';
	$: isHtmlFile = ['html', 'htm'].includes(fileExt);
	$: canPreview = !fileMeta?.is_binary
		? isMarkdownFile || isSvgFile || isHtmlFile || isImageFile
		: isImageFile;

	const loadImagePreviewBlob = async (cid: string, ws: string, path: string) => {
		revokePreviewBlob();
		const token = localStorage?.token;
		if (!token || !cid || !path) return;
		try {
			const res = await fetch(getEchoCodexDownloadUrl(cid, ws, path), {
				headers: { Authorization: `Bearer ${token}` }
			});
			if (!res.ok) return;
			const blob = await res.blob();
			previewBlobUrl = URL.createObjectURL(blob);
		} catch {
			// ignore
		}
	};

	let treeLoadSeq = 0;

	const loadTree = async () => {
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;
		const seq = ++treeLoadSeq;
		loadingTree = true;
		try {
			const res = await getEchoCodexTree(token, effectiveChatId, selectedWorkspace, '');
			if (seq !== treeLoadSeq) return;
			treeEntries = res?.entries || res || [];
		} catch (e: any) {
			if (seq !== treeLoadSeq) return;
			toast.error('Erreur de chargement Codex: ' + (e.message || ''));
		} finally {
			if (seq === treeLoadSeq) {
				loadingTree = false;
			}
		}
	};

	const selectFile = async (path: string) => {
		selectedPath = path;
		activeSubView = 'editor';
		viewMode = 'code';
		fileMeta = null;
		revokePreviewBlob();
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		loadingFile = true;
		try {
			const res = await getEchoCodexFile(token, effectiveChatId, selectedWorkspace, path);
			if (typeof res === 'string') {
				fileContent = res;
			} else {
				fileMeta = {
					is_binary: Boolean(res?.is_binary),
					mime: res?.mime_type || res?.mime,
					size_bytes: res?.size_bytes,
					hex_preview: res?.hex_preview
				};
				fileContent = res?.content ?? '';
				if (isImageFile) {
					viewMode = 'preview';
					void loadImagePreviewBlob(effectiveChatId, selectedWorkspace, path);
				}
			}
		} catch (e: any) {
			toast.error('Erreur lecture fichier: ' + (e.message || ''));
		} finally {
			loadingFile = false;
		}
	};

	const handleToggleViewMode = (mode: 'code' | 'preview') => {
		viewMode = mode;
		if (mode === 'preview' && isImageFile && !previewBlobUrl && selectedPath) {
			void loadImagePreviewBlob(effectiveChatId, selectedWorkspace, selectedPath);
		}
	};

	const handleSave = async () => {
		if (!isWritable || !selectedPath) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isSaving = true;
		try {
			await saveEchoCodexFile(token, effectiveChatId, {
				workspace: selectedWorkspace,
				path: selectedPath,
				content: fileContent,
				message: commitMessage.trim() || `Update ${selectedPath} via Codex UI`
			});
			toast.success('Fichier enregistré et commité avec succès');
			commitMessage = '';
			await loadTree();
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
		if (!token || !effectiveChatId) return;

		isDeleting = true;
		try {
			await deleteEchoCodexFile(token, effectiveChatId, selectedWorkspace, selectedPath);
			toast.success('Fichier supprimé');
			selectedPath = '';
			fileContent = '';
			fileMeta = null;
			revokePreviewBlob();
			await loadTree();
		} catch (e: any) {
			toast.error('Échec suppression: ' + (e.message || ''));
		} finally {
			isDeleting = false;
		}
	};

	const handleMkdir = async () => {
		if (!isWritable) return;
		const folderPath = prompt('Nom ou chemin du nouveau dossier :', '');
		if (!folderPath || !folderPath.trim()) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isMutating = true;
		try {
			await mkdirEchoCodex(token, effectiveChatId, selectedWorkspace, folderPath.trim());
			toast.success(`Dossier créé : ${folderPath.trim()}`);
			await loadTree();
		} catch (e: any) {
			toast.error('Échec création dossier: ' + (e.message || ''));
		} finally {
			isMutating = false;
		}
	};

	const handleRename = async () => {
		if (!isWritable || !selectedPath) return;
		const newPath = prompt('Nouveau chemin du fichier :', selectedPath);
		if (!newPath || !newPath.trim() || newPath.trim() === selectedPath) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isMutating = true;
		try {
			const cleanNew = newPath.trim();
			await renameEchoCodex(token, effectiveChatId, selectedWorkspace, selectedPath, cleanNew);
			toast.success(`Renommé en ${cleanNew}`);
			selectedPath = cleanNew;
			await loadTree();
		} catch (e: any) {
			toast.error('Échec renommage: ' + (e.message || ''));
		} finally {
			isMutating = false;
		}
	};

	const handleRestore = async (commitHash: string) => {
		if (!isWritable || !commitHash) return;
		const targetFile = selectedPath || prompt('Chemin du fichier à restaurer depuis ce commit :', '');
		if (!targetFile || !targetFile.trim()) return;
		if (!confirm(`Restaurer "${targetFile.trim()}" à l'état du commit ${commitHash.slice(0, 8)} ?`)) {
			return;
		}
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isMutating = true;
		try {
			await restoreEchoCodexFile(token, effectiveChatId, selectedWorkspace, targetFile.trim(), commitHash);
			toast.success(`Fichier restauré depuis ${commitHash.slice(0, 8)}`);
			await loadTree();
			await selectFile(targetFile.trim());
		} catch (e: any) {
			toast.error('Échec restauration: ' + (e.message || ''));
		} finally {
			isMutating = false;
		}
	};

	const handleDownload = async () => {
		if (!selectedPath || !effectiveChatId) return;
		const token = localStorage?.token;
		if (!token) return;
		try {
			const res = await fetch(getEchoCodexDownloadUrl(effectiveChatId, selectedWorkspace, selectedPath), {
				headers: { Authorization: `Bearer ${token}` }
			});
			if (!res.ok) throw new Error('HTTP ' + res.status);
			const blob = await res.blob();
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = selectedPath.split('/').pop() || 'download';
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (e: any) {
			toast.error('Échec téléchargement: ' + (e.message || ''));
		}
	};

	const loadLog = async () => {
		activeSubView = 'log';
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;
		try {
			const res = await getEchoCodexLog(token, effectiveChatId, selectedWorkspace, selectedPath, 30);
			logEntries = res?.logs || res?.commits || (Array.isArray(res) ? res : []);
		} catch (e: any) {
			toast.error('Erreur historique: ' + (e.message || ''));
		}
	};

	const loadDiff = async (commitHash: string) => {
		activeSubView = 'diff';
		currentCommit = commitHash;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;
		try {
			const res = await getEchoCodexDiff(token, effectiveChatId, selectedWorkspace, commitHash);
			diffText = typeof res === 'string' ? res : res?.diff || '';
		} catch (e: any) {
			toast.error('Erreur diff: ' + (e.message || ''));
		}
	};

	const exitCrossRead = () => {
		crossReadChatId = '';
		selectedPath = '';
		fileContent = '';
		fileMeta = null;
		revokePreviewBlob();
		void loadTree();
	};

	// Réaction au routage ciblé ($echoCodexTarget)
	$: if ($echoCodexTarget && $echoCodexTarget.chatId === chatId) {
		const target = $echoCodexTarget;
		echoCodexTarget.set(null);
		if (target.targetChatId && target.targetChatId !== chatId) {
			crossReadChatId = target.targetChatId;
		} else {
			crossReadChatId = '';
		}
		if (target.workspace && workspaces.some((w) => w.id === target.workspace)) {
			selectedWorkspace = target.workspace;
		}
		void (async () => {
			await loadTree();
			if (target.file) {
				await selectFile(target.file);
			}
			if (target.commitHash) {
				await loadDiff(target.commitHash);
			}
		})();
	}

	onMount(() => {
		if (effectiveChatId && $echoCodexTarget?.chatId !== chatId) void loadTree();
	});
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Bannière lecture croisée -->
	{#if isCrossRead}
		<div class="flex items-center justify-between gap-2 px-4 py-1.5 bg-amber-50 dark:bg-amber-950/40 border-b border-amber-200 dark:border-amber-800 text-xs text-amber-800 dark:text-amber-200 shrink-0">
			<span class="font-medium truncate">
				Lecture croisée (lecture seule) — Dépôt de la session <code class="font-mono">{crossReadChatId}</code>
			</span>
			<button
				type="button"
				on:click={exitCrossRead}
				class="px-2 py-0.5 rounded bg-amber-600 hover:bg-amber-700 text-white font-semibold shrink-0"
			>
				Revenir à cette session
			</button>
		</div>
	{/if}

	<!-- Barre de contrôle supérieure -->
	<div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0">
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
					fileMeta = null;
					revokePreviewBlob();
					void loadTree();
				}}
			>
				{#each workspaces as ws}
					<option value={ws.id}>{ws.label} {ws.writable && !isCrossRead ? '[RW]' : '[RO]'}</option>
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
				<div class="flex items-center gap-1">
					{#if isWritable}
						<button
							type="button"
							class="px-1.5 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-[11px]"
							on:click={handleMkdir}
							disabled={isMutating}
							title="Nouveau dossier"
						>
							📁+
						</button>
					{/if}
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
						{@const isDir = typeof entry === 'object' && entry?.type === 'dir'}
						<button
							type="button"
							class="w-full text-left px-2 py-1.5 rounded-md text-xs font-mono truncate transition flex items-center gap-1.5 {selectedPath === p ? 'bg-sky-500/10 text-sky-600 dark:text-sky-400 font-semibold' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'}"
							on:click={() => !isDir && selectFile(p)}
							title={p}
						>
							{#if isDir}
								<span class="text-xs flex-shrink-0">📁</span>
							{:else}
								<svg class="w-3.5 h-3.5 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
									<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
									<polyline points="14 2 14 8 20 8" />
								</svg>
							{/if}
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
					<div class="flex flex-wrap items-center justify-between gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50">
						<span class="font-mono text-xs font-semibold text-gray-800 dark:text-gray-200 truncate">
							{selectedPath}
						</span>

						<div class="flex flex-wrap items-center gap-1.5">
							{#if canPreview}
								<div class="flex items-center bg-gray-200/80 dark:bg-gray-800 p-0.5 rounded text-[11px] font-medium mr-1">
									{#if !fileMeta?.is_binary}
										<button
											type="button"
											on:click={() => handleToggleViewMode('code')}
											class="px-2 py-0.5 rounded transition {viewMode === 'code' ? 'bg-white dark:bg-gray-700 shadow-xs text-gray-900 dark:text-white' : 'text-gray-500'}"
										>
											Code
										</button>
									{/if}
									<button
										type="button"
										on:click={() => handleToggleViewMode('preview')}
										class="px-2 py-0.5 rounded transition {viewMode === 'preview' ? 'bg-white dark:bg-gray-700 shadow-xs text-gray-900 dark:text-white' : 'text-gray-500'}"
									>
										👁️ Aperçu
									</button>
								</div>
							{/if}

							<button
								type="button"
								on:click={handleDownload}
								class="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-xs font-medium text-gray-700 dark:text-gray-300 transition"
								title="Télécharger"
							>
								Télécharger
							</button>

							{#if isWritable}
								<button
									type="button"
									class="px-2 py-1 rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-xs font-medium text-gray-700 dark:text-gray-300 transition"
									on:click={handleRename}
									disabled={isMutating}
								>
									✏️ Renommer
								</button>
								<button
									type="button"
									class="px-2 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-red-500 text-xs font-medium transition"
									on:click={handleDelete}
									disabled={isDeleting}
								>
									{isDeleting ? '...' : 'Supprimer'}
								</button>
								{#if !fileMeta?.is_binary}
									<button
										type="button"
										class="px-3 py-1 rounded bg-sky-500 hover:bg-sky-600 text-white text-xs font-medium shadow-sm transition"
										on:click={handleSave}
										disabled={isSaving}
									>
										{isSaving ? 'Enregistrement...' : 'Enregistrer (Commit)'}
									</button>
								{/if}
							{/if}
						</div>
					</div>

					{#if isWritable && !fileMeta?.is_binary && viewMode === 'code'}
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
					<div class="flex-1 p-2 overflow-hidden flex flex-col">
						{#if viewMode === 'preview' && canPreview}
							{#if isImageFile}
								<div class="flex-1 flex items-center justify-center bg-gray-950/90 rounded-lg p-4 overflow-auto">
									{#if previewBlobUrl}
										<img src={previewBlobUrl} alt={selectedPath} class="max-w-full max-h-full object-contain rounded shadow-lg" />
									{:else}
										<Spinner className="w-6 h-6 text-sky-400" />
									{/if}
								</div>
							{:else if isMarkdownFile}
								<div class="flex-1 overflow-y-auto p-4 prose prose-sm dark:prose-invert max-w-none bg-white dark:bg-gray-950 rounded-lg border border-gray-200 dark:border-gray-800">
									<Markdown id={`codex-${selectedPath}`} content={fileContent} />
								</div>
							{:else if isSvgFile}
								<iframe
									title={selectedPath}
									sandbox=""
									srcdoc={`<!DOCTYPE html><html><body style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#111827;">${fileContent}</body></html>`}
									class="w-full h-full border border-gray-200 dark:border-gray-800 rounded-lg bg-gray-900"
								></iframe>
							{:else if isHtmlFile}
								<iframe
									title={selectedPath}
									sandbox="allow-scripts"
									srcdoc={fileContent}
									class="w-full h-full border border-gray-200 dark:border-gray-800 rounded-lg bg-white"
								></iframe>
							{/if}
						{:else if fileMeta?.is_binary}
							<div class="flex-1 flex flex-col p-4 overflow-auto space-y-3 bg-gray-50 dark:bg-gray-950 rounded-lg border border-gray-200 dark:border-gray-800">
								<div class="flex items-center justify-between text-xs text-gray-600 dark:text-gray-300">
									<span class="font-semibold">Fichier binaire ({fileMeta.mime || 'application/octet-stream'})</span>
									{#if fileMeta.size_bytes !== undefined}
										<span class="font-mono text-[11px] text-gray-400">{fileMeta.size_bytes} octets</span>
									{/if}
								</div>
								{#if fileMeta.hex_preview}
									<pre class="flex-1 overflow-auto p-3 font-mono text-[11px] leading-relaxed bg-gray-900 text-emerald-300 rounded-lg border border-gray-800">{fileMeta.hex_preview}</pre>
								{:else}
									<div class="text-xs text-gray-400">
										Aperçu hexadécimal indisponible. Utilisez « Télécharger » pour récupérer ce fichier.
									</div>
								{/if}
							</div>
						{:else}
							<textarea
								class="w-full h-full p-3 font-mono text-xs bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-sky-500 resize-none"
								bind:value={fileContent}
								readonly={!isWritable}
							></textarea>
						{/if}
					</div>
				{/if}
			{:else if activeSubView === 'log'}
				<div class="flex-1 overflow-y-auto p-4 space-y-2">
					<h3 class="text-xs font-bold text-gray-700 dark:text-gray-300 mb-2">Historique des modifications</h3>
					{#if logEntries.length === 0}
						<div class="text-gray-400 text-xs">Aucun commit trouvé pour ce chemin.</div>
					{:else}
						{#each logEntries as c}
							{@const commitId = c.hash_full || c.hash || c.commit_id || c.sha || ''}
							<div class="p-3 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-850/50 font-mono text-xs space-y-1">
								<div class="flex justify-between items-center gap-2">
									<button
										type="button"
										class="text-sky-500 font-bold hover:underline"
										on:click={() => loadDiff(commitId)}
									>
										{c.short_hash || commitId.slice(0, 8)}
									</button>
									<div class="flex items-center gap-2">
										<span class="text-[11px] text-gray-400">{c.author || ''}</span>
										{#if isWritable && commitId}
											<button
												type="button"
												on:click={() => void handleRestore(commitId)}
												disabled={isMutating}
												class="px-2 py-0.5 rounded bg-amber-500/15 hover:bg-amber-500/25 text-amber-700 dark:text-amber-300 font-sans text-[11px] font-medium transition"
											>
												⏮️ Restaurer ce commit
											</button>
										{/if}
									</div>
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
						<span class="text-xs font-bold text-gray-700 dark:text-gray-300">Modifications ({currentCommit.slice(0, 8)})</span>
						<div class="flex items-center gap-3">
							{#if isWritable && currentCommit}
								<button
									type="button"
									on:click={() => void handleRestore(currentCommit)}
									disabled={isMutating}
									class="px-2.5 py-1 rounded bg-amber-500/15 hover:bg-amber-500/25 text-amber-700 dark:text-amber-300 text-xs font-medium transition"
								>
									⏮️ Restaurer ce commit
								</button>
							{/if}
							<button
								type="button"
								class="text-xs text-sky-500 hover:underline"
								on:click={() => (activeSubView = 'log')}
							>
								Retour aux logs
							</button>
						</div>
					</div>
					<pre class="flex-1 overflow-auto p-3 font-mono text-xs bg-gray-950 text-emerald-400 rounded-xl border border-gray-800">{diffText || 'Aucune différence ou diff vide.'}</pre>
				</div>
			{/if}
		</div>
	</div>
</div>
