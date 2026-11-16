<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getEchoBrowserFrameImageUrl,
		getEchoBrowserFrames,
		type EchoBrowserFrame
	} from '$lib/apis/echo';
	import { echoBrowserLiveFrames, echoBrowserTarget } from '$lib/stores/echo';
	import { isSavedChatId } from '$lib/utils/chatId';

	export let chatId: string = '';

	let boundChatId = '';
	let loading = false;
	let persistedFrames: EchoBrowserFrame[] = [];
	let currentIndex = 0;
	let isLive = true;
	let isPlaying = false;
	let playbackSpeedMs = 1000;
	let playTimer: ReturnType<typeof setInterval> | null = null;

	// Cache local des Blob URLs authentifiés (révoqués sur onDestroy / changement de chat)
	let blobUrlCache: Record<string, string> = {};
	const pendingFetches = new Set<string>();

	// Zoom & Pan
	let zoom = 1;
	let panX = 0;
	let panY = 0;
	let isPanning = false;
	let panStartX = 0;
	let panStartY = 0;

	// Recadrage 8 poignées (coordonnées normalisées 0..1 sur l'image affichée)
	let cropMode = false;
	let cropRect = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };
	let cropDragHandle: string | null = null;
	let cropDragStart = { clientX: 0, clientY: 0, rect: { x: 0.1, y: 0.1, w: 0.8, h: 0.8 } };
	let imgElement: HTMLImageElement | null = null;

	const revokeAllBlobs = () => {
		for (const url of Object.values(blobUrlCache)) {
			try {
				URL.revokeObjectURL(url);
			} catch {
				// ignore
			}
		}
		blobUrlCache = {};
		pendingFetches.clear();
	};

	const stopPlayback = () => {
		isPlaying = false;
		if (playTimer !== null) {
			clearInterval(playTimer);
			playTimer = null;
		}
	};

	const loadPersistedFrames = async (cid: string) => {
		const token = localStorage?.token;
		if (!token || !cid || !isSavedChatId(cid)) {
			persistedFrames = [];
			return;
		}
		loading = true;
		try {
			const res = await getEchoBrowserFrames(token, cid);
			if (cid === boundChatId) {
				persistedFrames = Array.isArray(res?.frames) ? res.frames : [];
			}
		} catch {
			// Silencieux si session absente ou aucune capture
		} finally {
			if (cid === boundChatId) loading = false;
		}
	};

	let pendingTarget: { chatId: string; sessionId?: string; frameId?: string; frameIndex?: number } | null = null;

	const syncChat = (cid: string) => {
		if (cid === boundChatId) return;
		boundChatId = cid;
		pendingTarget = null;
		stopPlayback();
		revokeAllBlobs();
		persistedFrames = [];
		currentIndex = 0;
		isLive = true;
		zoom = 1;
		panX = 0;
		panY = 0;
		cropMode = false;
		if (cid) {
			void loadPersistedFrames(cid);
		}
	};

	onMount(() => {
		syncChat(chatId);
	});

	$: syncChat(chatId);

	onDestroy(() => {
		stopPlayback();
		revokeAllBlobs();
	});

	// Fusion réactive persistedFrames + $echoBrowserLiveFrames[chatId]
	$: liveFrames = chatId ? ($echoBrowserLiveFrames[chatId] ?? []) : [];
	$: frames = ((): EchoBrowserFrame[] => {
		const map = new Map<string, EchoBrowserFrame>();
		const ordered: EchoBrowserFrame[] = [];
		const pushFrame = (f: EchoBrowserFrame, idx: number) => {
			const key = f.file_id || f.live_frame_id || `idx_${idx}_${f.timestamp ?? 0}`;
			const existing = map.get(key);
			if (existing) {
				Object.assign(existing, f);
			} else {
				const copy = { ...f };
				map.set(key, copy);
				ordered.push(copy);
			}
		};
		persistedFrames.forEach((f, i) => pushFrame(f, i));
		liveFrames.forEach((f, i) => pushFrame(f, persistedFrames.length + i));
		return ordered;
	})();

	// Suivi automatique en mode EN DIRECT
	$: if (frames.length > 0) {
		if (isLive) {
			currentIndex = frames.length - 1;
		} else if (currentIndex >= frames.length) {
			currentIndex = frames.length - 1;
		}
	} else {
		currentIndex = 0;
	}

	// Capture de la requête $echoBrowserTarget sans la perdre si frames.length === 0 au démarrage à froid
	$: if ($echoBrowserTarget && $echoBrowserTarget.chatId === chatId) {
		pendingTarget = $echoBrowserTarget;
		echoBrowserTarget.set(null);
	}

	// Application de pendingTarget dès que les frames sont disponibles
	$: if (pendingTarget && pendingTarget.chatId === chatId && frames.length > 0) {
		const target = pendingTarget;
		pendingTarget = null;
		if (typeof target.frameIndex === 'number' && !Number.isNaN(target.frameIndex)) {
			const clamped = Math.max(0, Math.min(frames.length - 1, target.frameIndex));
			currentIndex = clamped;
			isLive = clamped === frames.length - 1;
			stopPlayback();
		} else if (target.frameId) {
			const idx = frames.findIndex(
				(f) => f.file_id === target.frameId || f.live_frame_id === target.frameId
			);
			if (idx >= 0) {
				currentIndex = idx;
				isLive = idx === frames.length - 1;
				stopPlayback();
			}
		} else if (target.sessionId) {
			let idx = -1;
			for (let i = frames.length - 1; i >= 0; i--) {
				if (frames[i].session_id === target.sessionId) {
					idx = i;
					break;
				}
			}
			if (idx >= 0) {
				currentIndex = idx;
				isLive = idx === frames.length - 1;
				stopPlayback();
			}
		}
	}

	$: activeFrame = frames[currentIndex] ?? null;

	const fetchAuthenticatedBlob = async (url: string) => {
		if (!url || blobUrlCache[url] || pendingFetches.has(url)) return;
		const token = localStorage?.token;
		if (!token) return;
		pendingFetches.add(url);
		try {
			const res = await fetch(url, {
				headers: { Authorization: `Bearer ${token}` }
			});
			if (!res.ok) return;
			const blob = await res.blob();
			const objUrl = URL.createObjectURL(blob);
			blobUrlCache = { ...blobUrlCache, [url]: objUrl };
		} catch {
			// Ignorer les erreurs réseau transitoires
		} finally {
			pendingFetches.delete(url);
		}
	};

	const getFrameImageSrc = (frame: EchoBrowserFrame | null, cache: Record<string, string>): string => {
		if (!frame) return '';
		const rawB64 = frame.live_b64 || frame.b64 || frame.webp_b64;
		if (rawB64) {
			if (rawB64.startsWith('data:')) return rawB64;
			const mime = frame.webp_b64 && !frame.b64 && !frame.live_b64 ? 'image/webp' : 'image/jpeg';
			return `data:${mime};base64,${rawB64}`;
		}
		const remoteUrl =
			frame.image_url || (frame.file_id && chatId ? getEchoBrowserFrameImageUrl(chatId, frame.file_id) : '');
		if (!remoteUrl) return '';
		if (cache[remoteUrl]) return cache[remoteUrl];
		void fetchAuthenticatedBlob(remoteUrl);
		return '';
	};

	$: activeImageSrc = getFrameImageSrc(activeFrame, blobUrlCache);
	$: isOnLatestLive = isLive && frames.length > 0 && currentIndex === frames.length - 1;

	const goToIndex = (idx: number) => {
		if (frames.length === 0) return;
		const clamped = Math.max(0, Math.min(frames.length - 1, idx));
		currentIndex = clamped;
		isLive = clamped === frames.length - 1 && !isPlaying;
	};

	const returnToLive = () => {
		stopPlayback();
		isLive = true;
		if (frames.length > 0) {
			currentIndex = frames.length - 1;
		}
	};

	const togglePlayback = () => {
		if (frames.length <= 1) return;
		if (isPlaying) {
			stopPlayback();
			return;
		}
		isLive = false;
		isPlaying = true;
		if (currentIndex >= frames.length - 1) {
			currentIndex = 0;
		}
		playTimer = setInterval(() => {
			if (currentIndex < frames.length - 1) {
				currentIndex += 1;
			} else {
				stopPlayback();
			}
		}, playbackSpeedMs);
	};

	const onSpeedChange = () => {
		if (isPlaying) {
			stopPlayback();
			togglePlayback();
		}
	};

	const resetZoom = () => {
		zoom = 1;
		panX = 0;
		panY = 0;
	};

	const adjustZoom = (delta: number) => {
		zoom = Math.max(0.1, Math.min(15, Number((zoom + delta).toFixed(2))));
	};

	const handleWheel = (e: WheelEvent) => {
		if (!e.ctrlKey) return;
		e.preventDefault();
		const factor = e.deltaY < 0 ? 1.15 : 0.87;
		zoom = Math.max(0.1, Math.min(15, Number((zoom * factor).toFixed(2))));
	};

	const handleMouseDown = (e: MouseEvent) => {
		if (cropMode || e.button !== 0 || !activeImageSrc) return;
		isPanning = true;
		panStartX = e.clientX - panX;
		panStartY = e.clientY - panY;
	};

	const handleWindowMouseMove = (e: MouseEvent) => {
		if (isPanning) {
			panX = e.clientX - panStartX;
			panY = e.clientY - panStartY;
			return;
		}
		if (cropDragHandle && imgElement) {
			const bounds = imgElement.getBoundingClientRect();
			if (bounds.width <= 0 || bounds.height <= 0) return;
			const dx = (e.clientX - cropDragStart.clientX) / bounds.width;
			const dy = (e.clientY - cropDragStart.clientY) / bounds.height;
			const r = { ...cropDragStart.rect };
			const minSize = 0.04;

			if (cropDragHandle === 'move') {
				r.x = Math.max(0, Math.min(1 - r.w, r.x + dx));
				r.y = Math.max(0, Math.min(1 - r.h, r.y + dy));
			} else {
				if (cropDragHandle.includes('w')) {
					const newX = Math.max(0, Math.min(r.x + r.w - minSize, r.x + dx));
					r.w = r.w + (r.x - newX);
					r.x = newX;
				}
				if (cropDragHandle.includes('e')) {
					r.w = Math.max(minSize, Math.min(1 - r.x, r.w + dx));
				}
				if (cropDragHandle.includes('n')) {
					const newY = Math.max(0, Math.min(r.y + r.h - minSize, r.y + dy));
					r.h = r.h + (r.y - newY);
					r.y = newY;
				}
				if (cropDragHandle.includes('s')) {
					r.h = Math.max(minSize, Math.min(1 - r.y, r.h + dy));
				}
			}
			cropRect = r;
		}
	};

	const handleWindowMouseUp = () => {
		isPanning = false;
		cropDragHandle = null;
	};

	const startCropDrag = (handle: string, e: MouseEvent) => {
		e.stopPropagation();
		e.preventDefault();
		cropDragHandle = handle;
		cropDragStart = { clientX: e.clientX, clientY: e.clientY, rect: { ...cropRect } };
	};

	const toggleCropMode = () => {
		if (!activeImageSrc) return;
		cropMode = !cropMode;
		if (cropMode) {
			cropRect = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };
		}
	};

	const renderToPngBlob = async (useCrop = false): Promise<Blob | null> => {
		if (!imgElement || !imgElement.complete || imgElement.naturalWidth === 0) return null;
		const nw = imgElement.naturalWidth;
		const nh = imgElement.naturalHeight;
		const sx = useCrop ? Math.round(cropRect.x * nw) : 0;
		const sy = useCrop ? Math.round(cropRect.y * nh) : 0;
		const sw = useCrop ? Math.max(1, Math.round(cropRect.w * nw)) : nw;
		const sh = useCrop ? Math.max(1, Math.round(cropRect.h * nh)) : nh;

		const canvas = document.createElement('canvas');
		canvas.width = sw;
		canvas.height = sh;
		const ctx = canvas.getContext('2d');
		if (!ctx) return null;
		ctx.drawImage(imgElement, sx, sy, sw, sh, 0, 0, sw, sh);
		return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), 'image/png'));
	};

	const copyImage = async (useCrop = false) => {
		const blob = await renderToPngBlob(useCrop);
		if (!blob) {
			toast.error('Image indisponible pour la copie.');
			return;
		}
		try {
			await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
			toast.success(useCrop ? 'Zone recadrée copiée dans le presse-papiers.' : 'Capture copiée dans le presse-papiers.');
		} catch {
			toast.error('Impossible de copier dans le presse-papiers.');
		}
	};

	const exportPng = async (useCrop = false) => {
		const blob = await renderToPngBlob(useCrop);
		if (!blob) {
			toast.error("Image indisponible pour l'export.");
			return;
		}
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		const ts = activeFrame?.timestamp ?? Math.floor(Date.now() / 1000);
		a.download = `echo-browser-${useCrop ? 'crop-' : ''}${ts}.png`;
		document.body.appendChild(a);
		a.click();
		a.remove();
		URL.revokeObjectURL(url);
		toast.success(useCrop ? 'Zone recadrée exportée en PNG.' : 'Capture exportée en PNG.');
	};

	const applyCrop = async () => {
		await exportPng(true);
		cropMode = false;
	};

	const formatTimestamp = (ts?: number): string => {
		if (!ts) return '';
		const d = new Date(ts > 1e12 ? ts : ts * 1000);
		if (Number.isNaN(d.getTime())) return '';
		return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	};
</script>

<svelte:window on:mousemove={handleWindowMouseMove} on:mouseup={handleWindowMouseUp} />

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden select-none">
	<!-- Barre supérieure : statut Live, URL et outils d'inspection -->
	<div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0">
		<div class="flex items-center gap-2 min-w-0 flex-1">
			{#if isOnLatestLive}
				<span class="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-red-500/15 text-red-600 dark:text-red-400 border border-red-500/30 shrink-0">
					🔴 EN DIRECT
				</span>
			{:else}
				<span class="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0">
					⏸️ DIFFÉRÉ
				</span>
			{/if}

			{#if activeFrame?.url}
				<span
					class="text-xs font-mono text-gray-600 dark:text-gray-300 truncate bg-white dark:bg-gray-900 px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700"
					title={activeFrame.url}
				>
					{activeFrame.url}
				</span>
			{:else}
				<span class="text-xs text-gray-400 truncate">Navigateur Web ECHO</span>
			{/if}

			{#if activeFrame?.timestamp}
				<span class="text-[11px] font-mono text-gray-400 shrink-0">
					{formatTimestamp(activeFrame.timestamp)}
				</span>
			{/if}

			{#if activeFrame?.retained || activeFrame?.status === 'retained'}
				<span class="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 shrink-0">
					📌 Conservée
				</span>
			{/if}
		</div>

		<div class="flex items-center gap-1 shrink-0">
			{#if !isOnLatestLive && frames.length > 0}
				<button
					type="button"
					on:click={returnToLive}
					class="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-red-600 hover:bg-red-700 text-white shadow-xs transition mr-1"
				>
					🔴 Retour au temps réel
				</button>
			{/if}

			<div class="flex items-center bg-gray-200/70 dark:bg-gray-800 rounded-lg p-0.5 text-[11px] font-mono">
				<button
					type="button"
					on:click={() => adjustZoom(-0.25)}
					class="px-1.5 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
					title="Zoom arrière"
				>-</button>
				<button
					type="button"
					on:click={resetZoom}
					class="px-2 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
					title="Réinitialiser zoom et position"
				>{Math.round(zoom * 100)}%</button>
				<button
					type="button"
					on:click={() => adjustZoom(0.25)}
					class="px-1.5 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
					title="Zoom avant"
				>+</button>
			</div>

			<button
				type="button"
				disabled={!activeImageSrc}
				on:click={toggleCropMode}
				class="px-2 py-1 rounded-lg text-xs font-medium border transition disabled:opacity-40 {cropMode
					? 'bg-sky-600 text-white border-sky-600'
					: 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700'}"
				title="Recadrer une sous-région"
			>
				✂️ Recadrer
			</button>
			<button
				type="button"
				disabled={!activeImageSrc}
				on:click={() => void copyImage(false)}
				class="px-2 py-1 rounded-lg text-xs font-medium bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 transition disabled:opacity-40"
				title="Copier l'image PNG"
			>
				📋 Copier
			</button>
			<button
				type="button"
				disabled={!activeImageSrc}
				on:click={() => void exportPng(false)}
				class="px-2 py-1 rounded-lg text-xs font-medium bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 transition disabled:opacity-40"
				title="Exporter en PNG"
			>
				💾 Exporter PNG
			</button>
		</div>
	</div>

	{#if cropMode}
		<div class="flex items-center justify-between px-3 py-1.5 bg-sky-50 dark:bg-sky-950/40 border-b border-sky-200 dark:border-sky-800 text-xs shrink-0">
			<span class="text-sky-800 dark:text-sky-200 font-medium">
				Mode recadrage actif — ajustez les 8 poignées puis appliquez pour exporter en pixels naturels.
			</span>
			<div class="flex items-center gap-2">
				<button
					type="button"
					on:click={() => (cropMode = false)}
					class="px-2.5 py-1 rounded bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100"
				>
					Annuler
				</button>
				<button
					type="button"
					on:click={() => void applyCrop()}
					class="px-2.5 py-1 rounded bg-sky-600 hover:bg-sky-700 text-white font-semibold"
				>
					Appliquer
				</button>
			</div>
		</div>
	{/if}

	<!-- Zone d'affichage de la capture -->
	<div
		class="relative flex-1 overflow-hidden bg-gray-950/95 flex items-center justify-center {cropMode ? 'cursor-crosshair' : isPanning ? 'cursor-grabbing' : 'cursor-grab'}"
		on:wheel={handleWheel}
		on:mousedown={handleMouseDown}
		role="region"
		aria-label="Visionneuse Navigateur Web ECHO"
	>
		{#if loading && frames.length === 0}
			<Spinner className="w-7 h-7 text-sky-400" />
		{:else if frames.length === 0}
			<div class="text-center p-6 text-gray-400 text-xs space-y-1.5">
				<div class="text-2xl">🌐</div>
				<div class="font-medium text-gray-300">Aucune capture navigateur pour cette session</div>
				<div>Les captures apparaîtront ici en direct dès qu'une navigation démarrera.</div>
			</div>
		{:else if !activeImageSrc}
			<Spinner className="w-6 h-6 text-sky-400" />
		{:else}
			<div
				class="relative inline-block"
				style="transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: center center;"
			>
				<img
					bind:this={imgElement}
					src={activeImageSrc}
					alt={activeFrame?.action || activeFrame?.url || 'Capture navigateur'}
					class="max-w-full max-h-[calc(100vh-14rem)] object-contain pointer-events-none select-none shadow-2xl"
					draggable="false"
				/>

				{#if cropMode}
					<!-- Overlay de recadrage 8 poignées -->
					<div
						class="absolute border-2 border-sky-400 bg-sky-400/10 shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] cursor-move"
						style="left: {cropRect.x * 100}%; top: {cropRect.y * 100}%; width: {cropRect.w * 100}%; height: {cropRect.h * 100}%;"
						on:mousedown={(e) => startCropDrag('move', e)}
						role="presentation"
					>
						{#each [
							{ id: 'nw', cls: '-top-1.5 -left-1.5 cursor-nwse-resize' },
							{ id: 'n', cls: '-top-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize' },
							{ id: 'ne', cls: '-top-1.5 -right-1.5 cursor-nesw-resize' },
							{ id: 'e', cls: 'top-1/2 -right-1.5 -translate-y-1/2 cursor-ew-resize' },
							{ id: 'se', cls: '-bottom-1.5 -right-1.5 cursor-nwse-resize' },
							{ id: 's', cls: '-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize' },
							{ id: 'sw', cls: '-bottom-1.5 -left-1.5 cursor-nesw-resize' },
							{ id: 'w', cls: 'top-1/2 -left-1.5 -translate-y-1/2 cursor-ew-resize' }
						] as handle (handle.id)}
							<div
								class="absolute w-3 h-3 bg-white border-2 border-sky-500 rounded-xs {handle.cls}"
								on:mousedown={(e) => startCropDrag(handle.id, e)}
								role="presentation"
							></div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Barre DVR Timeshift -->
	<div class="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0 text-xs">
		<div class="flex items-center gap-1">
			<button
				type="button"
				disabled={frames.length === 0 || currentIndex === 0}
				on:click={() => {
					stopPlayback();
					goToIndex(0);
				}}
				class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
				title="Début"
			>⏮</button>
			<button
				type="button"
				disabled={frames.length === 0 || currentIndex <= 0}
				on:click={() => {
					stopPlayback();
					goToIndex(currentIndex - 1);
				}}
				class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
				title="Précédent (-1)"
			>◀</button>
			<button
				type="button"
				disabled={frames.length <= 1}
				on:click={togglePlayback}
				class="px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-700 text-white font-semibold disabled:opacity-40"
				title={isPlaying ? 'Pause' : 'Lecture auto'}
			>{isPlaying ? '⏸' : '▶'}</button>

			<select
				bind:value={playbackSpeedMs}
				on:change={onSpeedChange}
				aria-label="Vitesse de lecture"
				class="rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-1.5 py-1 text-[11px] font-mono"
			>
				<option value={500}>0.5s</option>
				<option value={1000}>1s</option>
				<option value={2000}>2s</option>
				<option value={3000}>3s</option>
				<option value={5000}>5s</option>
				<option value={10000}>10s</option>
			</select>

			<button
				type="button"
				disabled={frames.length === 0 || currentIndex >= frames.length - 1}
				on:click={() => {
					stopPlayback();
					goToIndex(currentIndex + 1);
				}}
				class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
				title="Suivant (+1)"
			>▶</button>
			<button
				type="button"
				disabled={frames.length === 0 || currentIndex >= frames.length - 1}
				on:click={() => {
					stopPlayback();
					goToIndex(frames.length - 1);
				}}
				class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
				title="Fin"
			>⏭</button>
		</div>

		<input
			type="range"
			min={0}
			max={Math.max(0, frames.length - 1)}
			value={currentIndex}
			disabled={frames.length <= 1}
			on:input={(e) => {
				stopPlayback();
				goToIndex(Number(e.currentTarget.value));
			}}
			class="flex-1 min-w-[120px] accent-indigo-600"
			aria-label="Curseur temporel"
		/>

		<span class="font-mono text-[11px] text-gray-600 dark:text-gray-300 shrink-0">
			{frames.length > 0 ? `${currentIndex + 1} / ${frames.length}` : '0 / 0'}
		</span>
	</div>
</div>
