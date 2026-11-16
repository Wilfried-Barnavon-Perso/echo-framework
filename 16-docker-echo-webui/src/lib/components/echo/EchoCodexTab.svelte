<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import {
		getEchoToken,
		getEchoCodexTree,
		getEchoCodexFile,
		saveEchoCodexFile,
		deleteEchoCodexFile,
		getEchoCodexLog,
		getEchoCodexDiff,
		downloadEchoCodexFileBlob,
		mkdirEchoCodex,
		renameEchoCodex,
		restoreEchoCodexFile,
		uploadEchoCodexFile,
		aiEditEchoCodexFile,
		type EchoCodexAiEditPayload
	} from '$lib/apis/echo';
	import { ECHO_COGNITIVE_MODELS, echoCodexTarget } from '$lib/stores/echo';

	export let chatId: string = '';

	let currentChatId = '';
	let crossReadChatId = '';
	let workspaces = [
		{ id: 'main', label: 'Main', writable: true, versioned: true },
		{ id: 'sandbox', label: 'Sandbox', writable: true, versioned: false },
		{ id: 'plans', label: 'Plans', writable: false, versioned: true },
		{ id: 'generated', label: 'Generated', writable: false, versioned: true },
		{ id: 'files', label: 'Fichiers', writable: false, versioned: false }
	];

	let selectedWorkspace = 'main';
	let currentDirPath = '';
	let selectedPath = '';
	let fileContent = '';
	let savedFileContent = '';
	let fileMeta: {
		is_binary?: boolean;
		mime?: string;
		size_bytes?: number;
		hex_preview?: string;
	} | null = null;
	let viewMode: 'code' | 'preview' = 'code';
	let previewBlobUrl = '';

	let codeTextarea: HTMLTextAreaElement | null = null;
	let editorSelection = '';
	let aiInstruction = '';
	let aiModel = 'MODEL_APOLLO';
	let isAiEditing = false;
	let aiPendingContent: string | null = null;
	let aiLastInstruction = '';
	let aiUsedModel = '';
	let aiDiffViewMode: 'diff' | 'full' = 'diff';

	const AI_MODELS = [
		ECHO_COGNITIVE_MODELS.MODEL_APOLLO,
		ECHO_COGNITIVE_MODELS.MODEL_PROMETHEUS,
		ECHO_COGNITIVE_MODELS.MODEL_ATHENA,
		ECHO_COGNITIVE_MODELS.MODEL_ZEPHYR
	].map((m) => ({ id: m.key, label: m.label }));

	type QuickActionId = NonNullable<EchoCodexAiEditPayload['quick_action']>;
	const QUICK_ACTIONS: Array<{ id: QuickActionId; label: string }> = [
		{ id: 'shorter', label: '✂️ Raccourcir' },
		{ id: 'longer', label: '📝 Développer' },
		{ id: 'comment', label: '💬 Commenter' },
		{ id: 'uncomment', label: '🧹 Décommenter' },
		{ id: 'refactor', label: '♻️ Refactoriser' },
		{ id: 'fix', label: '🐛 Corriger' },
		{ id: 'tests', label: '🧪 Tests' },
		{ id: 'optimize', label: '⚡ Optimiser' }
	];

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
	let isUploading = false;
	let isDraggingOver = false;

	let splitWidth = 0;
	let userTreeWidth: number | null = null;
	let resizingTree = false;
	let resizeStartX = 0;
	let resizeStartWidth = 240;
	let fileUploadInput: HTMLInputElement | null = null;
	let previewLoadSeq = 0;

	$: effectiveChatId = crossReadChatId || chatId;
	$: isCrossRead = Boolean(crossReadChatId && crossReadChatId !== chatId);
	$: currentWsObj = workspaces.find((w) => w.id === selectedWorkspace) || workspaces[0];
	$: isWritable = currentWsObj.writable && !isCrossRead;
	$: isDirty = Boolean(
		selectedPath && isWritable && !fileMeta?.is_binary && fileContent !== savedFileContent
	);

	const resetAiState = () => {
		editorSelection = '';
		aiPendingContent = null;
		aiLastInstruction = '';
		aiUsedModel = '';
		aiDiffViewMode = 'diff';
	};

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
		currentDirPath = '';
		selectedPath = '';
		fileContent = '';
		savedFileContent = '';
		fileMeta = null;
		viewMode = 'code';
		aiInstruction = '';
		resetAiState();
		revokePreviewBlob();
		if ($echoCodexTarget?.chatId !== chatId) {
			void loadTree('');
		}
	}

	const getExt = (path: string): string => {
		const idx = path.lastIndexOf('.');
		return idx >= 0 ? path.slice(idx + 1).toLowerCase() : '';
	};

	const getEntryPath = (entry: any): string =>
		typeof entry === 'string'
			? entry.replace(/^\/+/, '')
			: String(entry?.path || entry?.filename || entry?.name || '').replace(/^\/+/, '');

	const getEntryName = (entry: any): string => {
		if (typeof entry === 'string') return entry.split('/').pop() || entry;
		const p = getEntryPath(entry);
		return String(entry?.name || (p ? p.split('/').pop() : '') || '');
	};

	const isDirEntry = (entry: any): boolean =>
		typeof entry === 'object' &&
		entry !== null &&
		['directory', 'dir', 'folder'].includes(String(entry?.type || '').toLowerCase());

	$: maxTreeWidth =
		splitWidth > 0 ? Math.max(200, Math.min(420, splitWidth - 260, Math.floor(splitWidth * 0.45))) : 420;
	$: autoTreeWidth = (() => {
		const maxChars = treeEntries.reduce((acc, entry) => Math.max(acc, getEntryName(entry).length), 14);
		return Math.max(200, Math.min(maxTreeWidth, Math.round(maxChars * 7.4 + 68)));
	})();
	$: effectiveTreeWidth = Math.max(180, Math.min(maxTreeWidth, userTreeWidth ?? autoTreeWidth));

	const startTreeResize = (event: PointerEvent) => {
		if (event.button !== 0) return;
		event.preventDefault();
		(event.currentTarget as HTMLElement)?.setPointerCapture?.(event.pointerId);
		resizingTree = true;
		resizeStartX = event.clientX;
		resizeStartWidth = effectiveTreeWidth;
	};

	const moveTreeResize = (event: PointerEvent) => {
		if (!resizingTree) return;
		userTreeWidth = Math.max(180, Math.min(maxTreeWidth, resizeStartWidth + event.clientX - resizeStartX));
	};

	const endTreeResize = () => {
		resizingTree = false;
	};

	const resetTreeResize = () => {
		userTreeWidth = null;
	};

	const handleResizerKeydown = (event: KeyboardEvent) => {
		if (event.key === 'Enter') {
			event.preventDefault();
			resetTreeResize();
			return;
		}
		if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
			event.preventDefault();
			const next =
				event.key === 'Home'
					? 180
					: event.key === 'End'
						? maxTreeWidth
						: effectiveTreeWidth + (event.key === 'ArrowRight' ? 16 : -16);
			userTreeWidth = Math.max(180, Math.min(maxTreeWidth, next));
		}
	};

	const parseCsvRows = (text: string, delimiter: string): string[][] => {
		const rows: string[][] = [];
		let row: string[] = [];
		let field = '';
		let inQuotes = false;
		for (let i = 0; i < text.length; i++) {
			const ch = text[i];
			if (inQuotes) {
				if (ch === '"') {
					if (text[i + 1] === '"') {
						field += '"';
						i++;
					} else {
						inQuotes = false;
					}
				} else {
					field += ch;
				}
			} else if (ch === '"') {
				inQuotes = true;
			} else if (ch === delimiter) {
				row.push(field);
				field = '';
			} else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
				if (ch === '\r') i++;
				row.push(field);
				field = '';
				if (row.some((c) => c !== '')) rows.push(row);
				row = [];
			} else {
				field += ch;
			}
		}
		row.push(field);
		if (row.some((c) => c !== '')) rows.push(row);
		return rows;
	};

	type PreviewKind = 'image' | 'pdf' | 'audio' | 'video' | 'markdown' | 'svg' | 'html' | 'csv' | 'none';

	const computePreviewCapabilities = (
		path: string,
		meta: { is_binary?: boolean; mime?: string } | null
	) => {
		const ext = getExt(path);
		const mime = String(meta?.mime || '').toLowerCase();
		const isBinary = Boolean(meta?.is_binary);

		const isImage =
			mime.startsWith('image/') ||
			['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif'].includes(ext);
		const isPdf = mime === 'application/pdf' || ext === 'pdf';
		const isAudio =
			mime.startsWith('audio/') || ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'].includes(ext);
		const isVideo =
			mime.startsWith('video/') || ['mp4', 'webm', 'ogv', 'mov'].includes(ext);
		const isMarkdown = !isBinary && ['md', 'markdown'].includes(ext);
		const isSvg = !isBinary && ext === 'svg';
		const isHtml = !isBinary && ['html', 'htm'].includes(ext);
		const isCsv = !isBinary && ['csv', 'tsv'].includes(ext);

		let kind: PreviewKind = 'none';
		if (isImage) kind = 'image';
		else if (isPdf) kind = 'pdf';
		else if (isAudio) kind = 'audio';
		else if (isVideo) kind = 'video';
		else if (isMarkdown) kind = 'markdown';
		else if (isSvg) kind = 'svg';
		else if (isHtml) kind = 'html';
		else if (isCsv) kind = 'csv';

		const needsBlob = isImage || isPdf || isAudio || isVideo;
		const canPreview = kind !== 'none';

		return {
			kind,
			needsBlob,
			canPreview,
			isImage,
			isPdf,
			isAudio,
			isVideo,
			isMarkdown,
			isSvg,
			isHtml,
			isCsv
		};
	};

	$: fileExt = getExt(selectedPath);
	$: previewCaps = computePreviewCapabilities(selectedPath, fileMeta);
	$: canPreview = previewCaps.canPreview;
	$: csvRows = previewCaps.kind === 'csv' ? parseCsvRows(fileContent, fileExt === 'tsv' ? '\t' : ',') : [];

	const loadPreviewBlob = async (cid: string, ws: string, path: string) => {
		const seq = ++previewLoadSeq;
		revokePreviewBlob();
		const token = getEchoToken();
		if (!token || !cid || !path) return;
		try {
			const blob = await downloadEchoCodexFileBlob(token, cid, ws, path);
			if (seq !== previewLoadSeq) return;
			previewBlobUrl = URL.createObjectURL(blob);
		} catch {
			// ignore
		}
	};

	let treeLoadSeq = 0;
	let fileLoadSeq = 0;

	const loadTree = async (dirPath: string = currentDirPath) => {
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;
		const seq = ++treeLoadSeq;
		loadingTree = true;
		try {
			const res = await getEchoCodexTree(token, effectiveChatId, selectedWorkspace, dirPath);
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

	const navigateToDir = async (dirPath: string) => {
		currentDirPath = dirPath;
		await loadTree(dirPath);
	};

	const selectFile = async (path: string, forceCodeMode = false) => {
		if (!path) return;
		const seq = ++fileLoadSeq;
		selectedPath = path;
		activeSubView = 'editor';
		viewMode = 'code';
		fileMeta = null;
		savedFileContent = '';
		resetAiState();
		revokePreviewBlob();
		const token = getEchoToken();
		if (!token || !effectiveChatId) return;

		loadingFile = true;
		try {
			const res = await getEchoCodexFile(token, effectiveChatId, selectedWorkspace, path);
			if (seq !== fileLoadSeq) return;
			if (typeof res === 'string') {
				fileContent = res;
				savedFileContent = res;
				const caps = computePreviewCapabilities(path, null);
				if (!forceCodeMode && caps.canPreview) {
					viewMode = 'preview';
				}
			} else {
				const nextMeta = {
					is_binary: Boolean(res?.is_binary),
					mime: res?.mime_type || res?.mime,
					size_bytes: res?.size_bytes,
					hex_preview: res?.hex_preview
				};
				fileMeta = nextMeta;
				fileContent = res?.content ?? '';
				savedFileContent = fileContent;
				const caps = computePreviewCapabilities(path, nextMeta);
				if (!forceCodeMode && caps.canPreview) {
					viewMode = 'preview';
					if (caps.needsBlob) {
						void loadPreviewBlob(effectiveChatId, selectedWorkspace, path);
					}
				}
			}
		} catch (e: any) {
			if (seq !== fileLoadSeq) return;
			toast.error('Erreur lecture fichier: ' + (e.message || ''));
		} finally {
			if (seq === fileLoadSeq) {
				loadingFile = false;
			}
		}
	};

	const handleToggleViewMode = (mode: 'code' | 'preview') => {
		viewMode = mode;
		if (mode === 'preview' && previewCaps.needsBlob && !previewBlobUrl && selectedPath) {
			void loadPreviewBlob(effectiveChatId, selectedWorkspace, selectedPath);
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
			savedFileContent = fileContent;
			toast.success(
				currentWsObj.versioned
					? 'Fichier enregistré et commité avec succès'
					: 'Fichier enregistré avec succès'
			);
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
			savedFileContent = '';
			fileMeta = null;
			resetAiState();
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
		const defaultPrefix = currentDirPath ? `${currentDirPath}/` : '';
		const folderPath = prompt('Nom ou chemin du nouveau dossier :', defaultPrefix);
		if (!folderPath || !folderPath.trim()) return;
		const cleanFolder = folderPath.trim().replace(/^\/+|\/+$/g, '');
		if (!cleanFolder) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isMutating = true;
		try {
			await mkdirEchoCodex(token, effectiveChatId, selectedWorkspace, cleanFolder);
			toast.success(`Dossier créé : ${cleanFolder}`);
			await loadTree(currentDirPath);
		} catch (e: any) {
			toast.error('Échec création dossier: ' + (e.message || ''));
		} finally {
			isMutating = false;
		}
	};

	const handleNewFile = async () => {
		if (!isWritable) return;
		const defaultPrefix = currentDirPath ? `${currentDirPath}/` : '';
		const filePath = prompt('Nom ou chemin du nouveau fichier :', defaultPrefix);
		if (!filePath || !filePath.trim()) return;
		const trimmed = filePath.trim().replace(/^\/+/, '');
		if (!trimmed || trimmed.endsWith('/')) {
			toast.error('Chemin de fichier invalide.');
			return;
		}
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isMutating = true;
		try {
			await saveEchoCodexFile(token, effectiveChatId, {
				workspace: selectedWorkspace,
				path: trimmed,
				content: '',
				message: `Création UI : ${trimmed}`
			});
			toast.success(`Fichier créé : ${trimmed}`);
			await loadTree(currentDirPath);
			await selectFile(trimmed, true);
		} catch (e: any) {
			toast.error('Échec création fichier: ' + (e.message || ''));
		} finally {
			isMutating = false;
		}
	};

	const uploadFilesList = async (files: FileList | File[]) => {
		if (!isWritable || !files || files.length === 0) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isUploading = true;
		let uploadedCount = 0;
		let lastUploadedPath = '';
		try {
			for (const file of Array.from(files)) {
				const res = await uploadEchoCodexFile(
					token,
					effectiveChatId,
					selectedWorkspace,
					file,
					currentDirPath
				);
				uploadedCount += 1;
				lastUploadedPath = res?.path || (currentDirPath ? `${currentDirPath}/${file.name}` : file.name);
			}
			if (uploadedCount > 0) {
				toast.success(
					uploadedCount === 1
						? `Fichier importé : ${lastUploadedPath}`
						: `${uploadedCount} fichiers importés`
				);
				await loadTree(currentDirPath);
				if (lastUploadedPath) {
					await selectFile(lastUploadedPath);
				}
			}
		} catch (e: any) {
			toast.error('Échec importation: ' + (e.message || ''));
		} finally {
			isUploading = false;
		}
	};

	const handleUploadChange = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		if (input?.files?.length) {
			await uploadFilesList(input.files);
		}
		if (input) input.value = '';
	};

	const handleTreeDrop = async (event: DragEvent) => {
		event.preventDefault();
		isDraggingOver = false;
		if (!isWritable) return;
		const files = event.dataTransfer?.files;
		if (files && files.length > 0) {
			await uploadFilesList(files);
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
		const token = getEchoToken();
		if (!token) return;
		try {
			const blob = await downloadEchoCodexFileBlob(token, effectiveChatId, selectedWorkspace, selectedPath);
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
		currentDirPath = '';
		selectedPath = '';
		fileContent = '';
		savedFileContent = '';
		fileMeta = null;
		viewMode = 'code';
		resetAiState();
		revokePreviewBlob();
		void loadTree('');
	};

	const updateSelectionFromTextarea = () => {
		if (!codeTextarea) return;
		const start = codeTextarea.selectionStart ?? 0;
		const end = codeTextarea.selectionEnd ?? 0;
		if (end > start) {
			editorSelection = codeTextarea.value.slice(start, end);
		} else {
			editorSelection = '';
		}
	};

	const handleEditorKeydown = (e: KeyboardEvent) => {
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
			e.preventDefault();
			void handleSave();
		}
	};

	const handleAiEdit = async (quickAction?: QuickActionId, quickLabel?: string) => {
		if (!isWritable || !selectedPath || fileMeta?.is_binary || isAiEditing) return;
		const instr = quickAction ? quickLabel || quickAction : aiInstruction.trim();
		if (!instr && !quickAction) return;
		const token = localStorage?.token;
		if (!token || !effectiveChatId) return;

		isAiEditing = true;
		try {
			const res = await aiEditEchoCodexFile(token, effectiveChatId, {
				workspace: selectedWorkspace,
				path: selectedPath,
				instruction: quickAction ? undefined : instr,
				quick_action: quickAction,
				content: fileContent,
				selection: editorSelection ? editorSelection : null,
				model: aiModel
			});
			aiLastInstruction = instr;
			aiUsedModel = res.model_used || aiModel;
			if (res.model_used && res.model_used !== aiModel && AI_MODELS.some((m) => m.id === res.model_used)) {
				aiModel = res.model_used;
				const usedLabel = AI_MODELS.find((m) => m.id === res.model_used)?.label || res.model_used;
				toast.success(`Proposition générée par ${usedLabel} (cascade)`);
			}
			aiPendingContent = res.modified_content;
			aiDiffViewMode = 'diff';
			if (!quickAction) {
				aiInstruction = '';
			}
		} catch (e: any) {
			toast.error('Échec édition IA: ' + (e.message || ''));
		} finally {
			isAiEditing = false;
		}
	};

	const acceptAiDiffAndSave = async () => {
		if (aiPendingContent === null) return;
		fileContent = aiPendingContent;
		if (currentWsObj.versioned) {
			commitMessage = `AI: ${aiLastInstruction.slice(0, 60)}`;
		}
		resetAiState();
		await handleSave();
	};

	const applyAiDiffToEditor = () => {
		if (aiPendingContent === null) return;
		fileContent = aiPendingContent;
		if (currentWsObj.versioned && !commitMessage.trim()) {
			commitMessage = `AI: ${aiLastInstruction.slice(0, 60)}`;
		}
		resetAiState();
	};

	const rejectAiDiff = () => {
		resetAiState();
	};

	interface DiffLine {
		type: 'same' | 'add' | 'del';
		text: string;
	}

	const computeLineDiff = (beforeText: string, afterText: string): DiffLine[] => {
		const aLines = beforeText.split('\n');
		const bLines = afterText.split('\n');
		const n = aLines.length;
		const m = bLines.length;

		// Fallback simple si > 2500 lignes ou matrice trop grande
		if (n > 2500 || m > 2500 || n * m > 2_500_000) {
			const out: DiffLine[] = [];
			const maxLen = Math.max(n, m);
			for (let i = 0; i < maxLen; i++) {
				const a = aLines[i];
				const b = bLines[i];
				if (a === b && a !== undefined) {
					out.push({ type: 'same', text: a });
				} else {
					if (a !== undefined) out.push({ type: 'del', text: a });
					if (b !== undefined) out.push({ type: 'add', text: b });
				}
			}
			return out;
		}

		// LCS DP compacte (Int32Array par ligne)
		const dp: Int32Array[] = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
		for (let i = n - 1; i >= 0; i--) {
			const row = dp[i];
			const nextRow = dp[i + 1];
			const aLine = aLines[i];
			for (let j = m - 1; j >= 0; j--) {
				if (aLine === bLines[j]) {
					row[j] = nextRow[j + 1] + 1;
				} else {
					row[j] = nextRow[j] >= row[j + 1] ? nextRow[j] : row[j + 1];
				}
			}
		}

		const result: DiffLine[] = [];
		let i = 0;
		let j = 0;
		while (i < n && j < m) {
			if (aLines[i] === bLines[j]) {
				result.push({ type: 'same', text: aLines[i] });
				i++;
				j++;
			} else if (dp[i + 1][j] >= dp[i][j + 1]) {
				result.push({ type: 'del', text: aLines[i] });
				i++;
			} else {
				result.push({ type: 'add', text: bLines[j] });
				j++;
			}
		}
		while (i < n) {
			result.push({ type: 'del', text: aLines[i++] });
		}
		while (j < m) {
			result.push({ type: 'add', text: bLines[j++] });
		}
		return result;
	};

	$: aiDiffLines = aiPendingContent !== null ? computeLineDiff(fileContent, aiPendingContent) : [];

	const getDiffLineClass = (line: string): string => {
		if (line.startsWith('+++') || line.startsWith('---')) {
			return 'text-gray-300 font-bold';
		}
		if (line.startsWith('@@')) {
			return 'text-cyan-400 bg-cyan-950/30';
		}
		if (line.startsWith('diff ') || line.startsWith('index ')) {
			return 'text-amber-300 font-semibold';
		}
		if (line.startsWith('+')) {
			return 'text-emerald-300 bg-emerald-950/35';
		}
		if (line.startsWith('-')) {
			return 'text-rose-300 bg-rose-950/35';
		}
		return 'text-gray-300';
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
		const targetFile = target.file ? String(target.file).replace(/^\/+/, '') : '';
		const lastSlash = targetFile.lastIndexOf('/');
		currentDirPath = lastSlash > 0 ? targetFile.slice(0, lastSlash) : '';
		void (async () => {
			await loadTree(currentDirPath);
			if (targetFile) {
				await selectFile(targetFile);
			}
			if (target.commitHash) {
				await loadDiff(target.commitHash);
			}
		})();
	}

	onMount(() => {
		if (effectiveChatId && $echoCodexTarget?.chatId !== chatId) void loadTree(currentDirPath);
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
					currentDirPath = '';
					selectedPath = '';
					fileContent = '';
					savedFileContent = '';
					fileMeta = null;
					viewMode = 'code';
					resetAiState();
					revokePreviewBlob();
					void loadTree('');
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
	<div class="flex flex-1 overflow-hidden" bind:clientWidth={splitWidth}>
		<!-- Explorateur arborescent (gauche) -->
		<div
			role="region"
			aria-label="Explorateur de fichiers Codex"
			style="width: {effectiveTreeWidth}px"
			class="shrink-0 border-r border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-950/40 flex flex-col relative transition-colors {isDraggingOver
				? 'ring-2 ring-inset ring-sky-500 bg-sky-500/5'
				: ''}"
			on:dragover={(e) => {
				if (!isWritable) return;
				e.preventDefault();
				isDraggingOver = true;
			}}
			on:dragleave={() => (isDraggingOver = false)}
			on:drop={handleTreeDrop}
		>
			<div class="p-2 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center text-xs font-semibold text-gray-600 dark:text-gray-400 gap-1">
				<span class="truncate">Fichiers</span>
				<div class="flex items-center gap-0.5 shrink-0">
					{#if isWritable}
						<button
							type="button"
							class="px-1.5 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-[11px]"
							on:click={handleNewFile}
							disabled={isMutating || isUploading}
							title="Nouveau fichier"
						>
							📄+
						</button>
						<button
							type="button"
							class="px-1.5 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-[11px]"
							on:click={handleMkdir}
							disabled={isMutating || isUploading}
							title="Nouveau dossier"
						>
							📁+
						</button>
						<button
							type="button"
							class="px-1.5 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-[11px]"
							on:click={() => fileUploadInput?.click()}
							disabled={isMutating || isUploading}
							title="Importer des fichiers"
						>
							{isUploading ? '⏳' : '⬆️'}
						</button>
						<input
							type="file"
							multiple
							class="hidden"
							bind:this={fileUploadInput}
							on:change={handleUploadChange}
						/>
					{/if}
					<button
						type="button"
						class="p-1 hover:text-sky-500"
						on:click={() => void loadTree(currentDirPath)}
						title="Rafraîchir l'arbre"
					>
						<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
						</svg>
					</button>
				</div>
			</div>

			{#if currentDirPath}
				{@const dirSegments = currentDirPath.split('/').filter(Boolean)}
				<div class="px-2 py-1 border-b border-gray-200 dark:border-gray-800 bg-gray-100/60 dark:bg-gray-900/60 flex items-center gap-1 text-[11px] font-mono overflow-x-auto shrink-0">
					<button
						type="button"
						class="px-1 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-sky-600 dark:text-sky-400 font-bold shrink-0"
						on:click={() => void navigateToDir(dirSegments.slice(0, -1).join('/'))}
						title="Remonter d'un niveau"
					>
						..
					</button>
					<button
						type="button"
						class="px-1 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-500 shrink-0"
						on:click={() => void navigateToDir('')}
						title="Racine"
					>
						/
					</button>
					{#each dirSegments as seg, idx}
						{@const segPath = dirSegments.slice(0, idx + 1).join('/')}
						<span class="text-gray-400">/</span>
						<button
							type="button"
							class="px-1 py-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-800 truncate {idx === dirSegments.length - 1
								? 'text-gray-900 dark:text-gray-100 font-semibold'
								: 'text-gray-600 dark:text-gray-400'}"
							on:click={() => void navigateToDir(segPath)}
							title={segPath}
						>
							{seg}
						</button>
					{/each}
				</div>
			{/if}

			<div class="flex-1 overflow-y-auto p-1 space-y-0.5">
				{#if loadingTree}
					<div class="p-4 flex justify-center">
						<Spinner className="w-5 h-5 text-sky-500" />
					</div>
				{:else if treeEntries.length === 0}
					<div class="p-4 text-center text-xs text-gray-400 space-y-2">
						<div>{currentDirPath ? 'Dossier vide.' : 'Aucun fichier dans ce workspace.'}</div>
						{#if isWritable}
							<div class="flex items-center justify-center gap-1.5">
								<button
									type="button"
									class="px-2 py-1 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-600 dark:text-sky-400 text-[11px] font-medium"
									on:click={handleNewFile}
								>
									+ Fichier
								</button>
								<button
									type="button"
									class="px-2 py-1 rounded bg-gray-200/70 dark:bg-gray-800 hover:bg-gray-300/70 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 text-[11px] font-medium"
									on:click={() => fileUploadInput?.click()}
								>
									Importer
								</button>
							</div>
						{/if}
					</div>
				{:else}
					{#each treeEntries as entry}
						{@const entryPath = getEntryPath(entry)}
						{@const entryName = getEntryName(entry)}
						{@const isDir = isDirEntry(entry)}
						<button
							type="button"
							class="w-full text-left px-2 py-1.5 rounded-md text-xs font-mono truncate transition flex items-center gap-1.5 {selectedPath === entryPath
								? 'bg-sky-500/10 text-sky-600 dark:text-sky-400 font-semibold'
								: 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'}"
							on:click={() => (isDir ? navigateToDir(entryPath) : selectFile(entryPath))}
							title={entryPath}
						>
							{#if isDir}
								<span class="text-xs flex-shrink-0">📁</span>
							{:else}
								<svg class="w-3.5 h-3.5 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
									<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
									<polyline points="14 2 14 8 20 8" />
								</svg>
							{/if}
							<span class="truncate">{entryName || entryPath}</span>
						</button>
					{/each}
				{/if}
			</div>
		</div>

		<!-- Poignée de redimensionnement -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
		<div
			role="separator"
			aria-orientation="vertical"
			aria-label="Redimensionner la colonne Fichiers"
			aria-valuemin={180}
			aria-valuemax={maxTreeWidth}
			aria-valuenow={Math.round(effectiveTreeWidth)}
			tabindex="0"
			class="w-1.5 shrink-0 cursor-col-resize hover:bg-sky-500/40 active:bg-sky-500/60 transition-colors {resizingTree
				? 'bg-sky-500/60'
				: 'bg-transparent'}"
			on:pointerdown={startTreeResize}
			on:pointermove={moveTreeResize}
			on:pointerup={endTreeResize}
			on:pointercancel={endTreeResize}
			on:lostpointercapture={endTreeResize}
			on:dblclick={resetTreeResize}
			on:keydown={handleResizerKeydown}
			title="Glisser pour redimensionner • Double-clic pour ajustement automatique"
		></div>

		<!-- Zone Principale (droite) -->
		<div class="flex-1 min-w-0 flex flex-col overflow-hidden bg-white dark:bg-gray-900">
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
						<div class="flex items-center gap-2 min-w-0">
							<span class="font-mono text-xs font-semibold text-gray-800 dark:text-gray-200 truncate">
								{selectedPath}
							</span>
							{#if isDirty}
								<span
									class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-300 text-[10px] font-semibold shrink-0"
									title="Modifications non enregistrées (Ctrl+S pour enregistrer)"
								>
									● Modifié
								</span>
							{/if}
						</div>

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
										{isSaving
											? 'Enregistrement...'
											: currentWsObj.versioned
												? 'Enregistrer (Commit)'
												: 'Enregistrer'}
									</button>
								{/if}
							{/if}
						</div>
					</div>

					{#if isWritable && currentWsObj.versioned && !fileMeta?.is_binary && viewMode === 'code'}
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
							{#if previewCaps.kind === 'image'}
								<div class="flex-1 flex items-center justify-center bg-gray-950/90 rounded-lg p-4 overflow-auto">
									{#if previewBlobUrl}
										<img src={previewBlobUrl} alt={selectedPath} class="max-w-full max-h-full object-contain rounded shadow-lg" />
									{:else}
										<Spinner className="w-6 h-6 text-sky-400" />
									{/if}
								</div>
							{:else if previewCaps.kind === 'pdf'}
								<div class="flex-1 flex flex-col rounded-lg border border-gray-200 dark:border-gray-800 overflow-hidden bg-gray-900">
									{#if previewBlobUrl}
										<iframe title={selectedPath} src={previewBlobUrl} class="w-full h-full border-0"></iframe>
									{:else}
										<div class="flex-1 flex items-center justify-center">
											<Spinner className="w-6 h-6 text-sky-400" />
										</div>
									{/if}
								</div>
							{:else if previewCaps.kind === 'audio'}
								<div class="flex-1 flex flex-col items-center justify-center gap-3 bg-gray-50 dark:bg-gray-950 rounded-lg border border-gray-200 dark:border-gray-800 p-6">
									<span class="font-mono text-xs text-gray-500">{selectedPath}</span>
									{#if previewBlobUrl}
										<audio controls src={previewBlobUrl} class="w-full max-w-md"></audio>
									{:else}
										<Spinner className="w-6 h-6 text-sky-400" />
									{/if}
								</div>
							{:else if previewCaps.kind === 'video'}
								<div class="flex-1 flex items-center justify-center bg-gray-950 rounded-lg p-4 overflow-auto">
									{#if previewBlobUrl}
										<!-- svelte-ignore a11y_media_has_caption -->
										<video controls src={previewBlobUrl} class="max-w-full max-h-full rounded shadow-lg"></video>
									{:else}
										<Spinner className="w-6 h-6 text-sky-400" />
									{/if}
								</div>
							{:else if previewCaps.kind === 'markdown'}
								<div class="flex-1 overflow-y-auto p-4 prose prose-sm dark:prose-invert max-w-none bg-white dark:bg-gray-950 rounded-lg border border-gray-200 dark:border-gray-800">
									<Markdown id={`codex-${selectedPath}`} content={fileContent} />
								</div>
							{:else if previewCaps.kind === 'svg'}
								<iframe
									title={selectedPath}
									sandbox=""
									srcdoc={`<!DOCTYPE html><html><body style="margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#111827;">${fileContent}</body></html>`}
									class="w-full h-full border border-gray-200 dark:border-gray-800 rounded-lg bg-gray-900"
								></iframe>
							{:else if previewCaps.kind === 'html'}
								<iframe
									title={selectedPath}
									sandbox="allow-scripts"
									srcdoc={fileContent}
									class="w-full h-full border border-gray-200 dark:border-gray-800 rounded-lg bg-white"
								></iframe>
							{:else if previewCaps.kind === 'csv'}
								<div class="flex-1 overflow-auto bg-white dark:bg-gray-950 rounded-lg border border-gray-200 dark:border-gray-800">
									{#if csvRows.length === 0}
										<div class="p-4 text-xs text-gray-400">Tableau vide.</div>
									{:else}
										<table class="table-auto w-full border-collapse text-xs">
											<thead class="bg-gray-100 dark:bg-gray-900 sticky top-0">
												<tr>
													{#each csvRows[0] as header}
														<th class="px-3 py-1.5 text-left font-semibold border-b border-r border-gray-200 dark:border-gray-800 text-gray-700 dark:text-gray-200 whitespace-nowrap">
															{header}
														</th>
													{/each}
												</tr>
											</thead>
											<tbody>
												{#each csvRows.slice(1) as row}
													<tr class="hover:bg-gray-50 dark:hover:bg-gray-900/50">
														{#each row as cell}
															<td class="px-3 py-1 border-b border-r border-gray-100 dark:border-gray-800/70 font-mono text-gray-700 dark:text-gray-300 whitespace-nowrap">
																{cell}
															</td>
														{/each}
													</tr>
												{/each}
											</tbody>
										</table>
									{/if}
								</div>
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
						{:else if aiPendingContent !== null}
							<div class="flex-1 flex flex-col overflow-hidden rounded-lg border border-sky-500/40 bg-gray-950">
								<div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 bg-gray-900 border-b border-gray-800 text-xs shrink-0">
									<div class="flex items-center gap-2 min-w-0">
										<span class="font-semibold text-sky-400">✨ Proposition IA</span>
										{#if aiUsedModel}
											<span class="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 font-mono text-[10px]">
												{AI_MODELS.find((m) => m.id === aiUsedModel)?.label || aiUsedModel}
											</span>
										{/if}
										{#if aiLastInstruction}
											<span class="text-gray-400 truncate max-w-[260px]" title={aiLastInstruction}>
												« {aiLastInstruction} »
											</span>
										{/if}
									</div>
									<div class="flex flex-wrap items-center gap-1.5">
										<div class="flex items-center bg-gray-800 p-0.5 rounded text-[11px] mr-1">
											<button
												type="button"
												on:click={() => (aiDiffViewMode = 'diff')}
												class="px-2 py-0.5 rounded transition {aiDiffViewMode === 'diff'
													? 'bg-gray-700 text-white'
													: 'text-gray-400 hover:text-gray-200'}"
											>
												Diff IA
											</button>
											<button
												type="button"
												on:click={() => (aiDiffViewMode = 'full')}
												class="px-2 py-0.5 rounded transition {aiDiffViewMode === 'full'
													? 'bg-gray-700 text-white'
													: 'text-gray-400 hover:text-gray-200'}"
											>
												Texte complet
											</button>
										</div>
										<button
											type="button"
											on:click={() => void acceptAiDiffAndSave()}
											disabled={isSaving}
											class="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold transition"
										>
											✅ Accepter & {currentWsObj.versioned ? 'Commiter' : 'Enregistrer'}
										</button>
										<button
											type="button"
											on:click={applyAiDiffToEditor}
											disabled={isSaving}
											class="px-2.5 py-1 rounded bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium transition"
										>
											✏️ Appliquer dans l'éditeur
										</button>
										<button
											type="button"
											on:click={rejectAiDiff}
											disabled={isSaving}
											class="px-2.5 py-1 rounded bg-rose-600/80 hover:bg-rose-600 text-white text-xs font-medium transition"
										>
											❌ Rejeter
										</button>
									</div>
								</div>

								{#if aiDiffViewMode === 'diff'}
									<div class="flex-1 overflow-auto p-2 font-mono text-xs leading-relaxed">
										{#each aiDiffLines as line}
											<div
												class="px-2 whitespace-pre-wrap break-all {line.type === 'add'
													? 'bg-emerald-950/45 text-emerald-300'
													: line.type === 'del'
														? 'bg-rose-950/45 text-rose-300'
														: 'text-gray-400'}"
											>
												<span class="inline-block w-4 select-none opacity-70">{line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' '}</span>{line.text}
											</div>
										{/each}
									</div>
								{:else}
									<pre class="flex-1 overflow-auto p-3 font-mono text-xs text-gray-200 whitespace-pre-wrap break-all">{aiPendingContent}</pre>
								{/if}
							</div>
						{:else}
							<textarea
								bind:this={codeTextarea}
								class="w-full h-full p-3 font-mono text-xs bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-sky-500 resize-none"
								bind:value={fileContent}
								readonly={!isWritable}
								on:select={updateSelectionFromTextarea}
								on:keyup={updateSelectionFromTextarea}
								on:mouseup={updateSelectionFromTextarea}
								on:keydown={handleEditorKeydown}
							></textarea>
						{/if}
					</div>

					{#if isWritable && !fileMeta?.is_binary && viewMode === 'code' && selectedPath && aiPendingContent === null}
						<div class="px-3 py-2 border-t border-gray-200 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/90 space-y-1.5 shrink-0">
							<!-- Quick Actions -->
							<div class="flex flex-wrap items-center gap-1">
								{#each QUICK_ACTIONS as qa}
									<button
										type="button"
										disabled={isAiEditing}
										on:click={() => void handleAiEdit(qa.id, qa.label)}
										class="px-2 py-0.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-sky-500/10 hover:border-sky-500/40 text-[11px] text-gray-700 dark:text-gray-300 disabled:opacity-50 transition"
									>
										{qa.label}
									</button>
								{/each}
							</div>

							<!-- Barre d'instruction IA one-shot -->
							<div class="flex flex-wrap items-center gap-1.5">
								<select
									bind:value={aiModel}
									disabled={isAiEditing}
									title="Modèle IA"
									class="text-xs bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-md px-2 py-1 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-sky-500"
								>
									{#each AI_MODELS as m}
										<option value={m.id}>{m.label}</option>
									{/each}
								</select>

								{#if editorSelection}
									<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-sky-500/15 text-sky-600 dark:text-sky-300 text-[11px] font-medium shrink-0">
										Sélection ({editorSelection.length} car.)
										<button
											type="button"
											on:click={() => (editorSelection = '')}
											class="hover:text-sky-800 dark:hover:text-white font-bold leading-none"
											title="Repasser au fichier entier"
										>
											×
										</button>
									</span>
								{:else}
									<span class="px-2 py-0.5 rounded bg-gray-200/70 dark:bg-gray-800 text-gray-500 dark:text-gray-400 text-[11px] shrink-0">
										Fichier entier
									</span>
								{/if}

								<input
									type="text"
									bind:value={aiInstruction}
									disabled={isAiEditing}
									placeholder="Instruction IA one-shot (ex: Ajoute la gestion d'erreurs)..."
									class="flex-1 min-w-[180px] text-xs bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-md px-2.5 py-1 text-gray-800 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-sky-500"
									on:keydown={(e) => {
										if (e.key === 'Enter' && !e.shiftKey) {
											e.preventDefault();
											void handleAiEdit();
										}
									}}
								/>

								<button
									type="button"
									disabled={isAiEditing || !aiInstruction.trim()}
									on:click={() => void handleAiEdit()}
									class="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-sky-500 hover:bg-sky-600 disabled:opacity-50 text-white text-xs font-semibold transition shrink-0"
								>
									{#if isAiEditing}
										<Spinner className="w-3.5 h-3.5" />
										<span>Édition...</span>
									{:else}
										<span>✨ Éditer</span>
									{/if}
								</button>
							</div>
						</div>
					{/if}
				{/if}
			{:else if activeSubView === 'log'}
				<div class="flex-1 overflow-y-auto p-4 space-y-2">
					<h3 class="text-xs font-bold text-gray-700 dark:text-gray-300 mb-2">Historique des modifications</h3>
					{#if !currentWsObj.versioned}
						<div class="p-3 rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 text-xs text-gray-500 dark:text-gray-400">
							L'espace « {currentWsObj.label} » n'est pas versionné par Git (aucun historique de commits).
						</div>
					{:else if logEntries.length === 0}
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
					<div class="flex-1 overflow-auto p-3 font-mono text-xs bg-gray-950 rounded-xl border border-gray-800 leading-relaxed">
						{#if !diffText}
							<div class="text-gray-400">Aucune différence ou diff vide.</div>
						{:else}
							{#each diffText.split('\n') as line}
								<div class="px-1.5 whitespace-pre-wrap break-all {getDiffLineClass(line)}">{line}</div>
							{/each}
						{/if}
					</div>
				</div>
			{/if}
		</div>
	</div>
</div>
