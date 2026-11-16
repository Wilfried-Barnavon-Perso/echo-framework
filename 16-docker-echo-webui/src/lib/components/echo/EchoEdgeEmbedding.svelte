<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { mobile } from '$lib/stores';
	import {
		echoEdgeEmbedState,
		echoEdgeEmbedProgress,
		echoEdgeEmbedReason
	} from '$lib/stores/echo';

	export let mode: 'bridge' | 'badge' = 'bridge';

	const TARGET_REPO = 'onnx-community/harrier-oss-v1-0.6b-ONNX';
	const TARGET_DTYPE = 'fp16';
	const MIN_BUFFER_BYTES = 134217728; // 128 Mo

	let ws: WebSocket | null = null;
	let clientId = '';
	let showPopover = false;
	let containerRef: HTMLDivElement | null = null;
	let destroyed = false;

	const probeWebGPU = async (): Promise<{ compatible: boolean; reason?: string }> => {
		if (typeof navigator === 'undefined' || !(navigator as any).gpu) {
			return { compatible: false, reason: 'WebGPU non exposé par le navigateur' };
		}
		let adapter: any = null;
		try {
			adapter = await (navigator as any).gpu.requestAdapter({ powerPreference: 'high-performance' });
		} catch (e: any) {
			return { compatible: false, reason: 'Erreur lors de requestAdapter: ' + e?.message };
		}
		if (!adapter) {
			return { compatible: false, reason: 'Adaptateur WebGPU refusé' };
		}
		if (adapter.isFallbackAdapter || (adapter.info && adapter.info.isFallbackAdapter)) {
			return { compatible: false, reason: 'Adaptateur WebGPU logiciel (fallback) rejeté' };
		}
		if (!adapter.features || !adapter.features.has('shader-f16')) {
			return { compatible: false, reason: 'Extension matérielle shader-f16 non supportée' };
		}
		const limits = adapter.limits || {};
		if (
			(limits.maxBufferSize && limits.maxBufferSize < MIN_BUFFER_BYTES) ||
			(limits.maxStorageBufferBindingSize && limits.maxStorageBufferBindingSize < MIN_BUFFER_BYTES)
		) {
			return { compatible: false, reason: 'Limites de buffer VRAM insuffisantes (< 128 Mo)' };
		}

		try {
			const device = await adapter.requestDevice({ requiredFeatures: ['shader-f16'] });
			if (!device) {
				return { compatible: false, reason: 'Allocation GPUDevice refusée' };
			}
			device.destroy();
		} catch (e: any) {
			return { compatible: false, reason: 'Test smoke shader-f16 échoué: ' + e?.message };
		}
		return { compatible: true };
	};

	const handleVisibilityChange = () => {
		if (!ws || ws.readyState !== WebSocket.OPEN) return;
		const state = document.hidden ? 'idle' : 'active';
		ws.send(JSON.stringify({ type: 'visibility', state }));
	};

	const handleClickOutside = (e: MouseEvent) => {
		if (showPopover && containerRef && !containerRef.contains(e.target as Node)) {
			showPopover = false;
		}
	};

	onMount(async () => {
		if (mode === 'badge') {
			window.addEventListener('click', handleClickOutside);
			return;
		}

		if ($mobile) {
			echoEdgeEmbedState.set('incompatible');
			echoEdgeEmbedReason.set('Appareil mobile détecté');
			return;
		}

		echoEdgeEmbedState.set('probing');
		echoEdgeEmbedProgress.set(0);
		echoEdgeEmbedReason.set('');
		clientId = crypto.randomUUID();

		const probe = await probeWebGPU();
		if (destroyed) return;
		if (!probe.compatible) {
			echoEdgeEmbedState.set('incompatible');
			echoEdgeEmbedReason.set(probe.reason || 'WebGPU incompatible');
			// Notifier l'embedding worker via WS court
			try {
				const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
				const quickWs = new WebSocket(
					`${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`
				);
				quickWs.onopen = () => {
					quickWs.send(JSON.stringify({ type: 'incompatible', reason: probe.reason }));
					setTimeout(() => quickWs.close(), 100);
				};
				quickWs.onerror = () => {
					try {
						quickWs.close();
					} catch {
						// ignore
					}
				};
			} catch (e) {}
			return;
		}

		echoEdgeEmbedState.set('downloading');
		echoEdgeEmbedProgress.set(0);

		try {
			const tfUrl =
				'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js';
			const transformers = await import(/* @vite-ignore */ tfUrl);
			if (destroyed) return;
			transformers.env.allowLocalModels = false;

			const tokenizer = await transformers.AutoTokenizer.from_pretrained(TARGET_REPO);
			if (destroyed) return;
			const model = await transformers.AutoModel.from_pretrained(TARGET_REPO, {
				device: 'webgpu',
				dtype: TARGET_DTYPE,
				progress_callback: (x: any) => {
					if (destroyed) return;
					if (x.status === 'downloading' || x.status === 'progress') {
						const pct = x.total ? Math.round((x.loaded / x.total) * 100) : 0;
						echoEdgeEmbedProgress.set(pct);
					}
				}
			});
			if (destroyed) return;

			echoEdgeEmbedProgress.set(100);
			echoEdgeEmbedReason.set('');
			echoEdgeEmbedState.set('ready');

			const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
			const wsUrl = `${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`;
			ws = new WebSocket(wsUrl);

			ws.onopen = () => {
				if (destroyed) {
					try {
						ws?.close();
					} catch {}
					return;
				}
				ws?.send(JSON.stringify({ type: 'ready' }));
				if (!document.hidden) {
					ws?.send(JSON.stringify({ type: 'visibility', state: 'active' }));
				}
			};

			document.addEventListener('visibilitychange', handleVisibilityChange);

			ws.onmessage = async (event) => {
				try {
					const payload = JSON.parse(event.data);
					if (payload.type === 'embed' && payload.texts && payload.request_id) {
						echoEdgeEmbedState.set('active');
						const texts = Array.isArray(payload.texts) ? payload.texts : [payload.texts];
						const inputs = tokenizer(texts, { padding: true, truncation: true });
						const outputs = await model(inputs);
						if (destroyed) return;

						const tensor =
							outputs.sentence_embedding || outputs.last_hidden_state || Object.values(outputs)[0];
						const dims = tensor.dims;
						const batchSize = dims[0];
						const is3D = dims.length >= 3;
						const seqLen = is3D ? dims[1] : 1;
						const embedDim = is3D ? dims[2] : dims[1];

						const embeddings: number[][] = [];
						for (let i = 0; i < batchSize; i++) {
							const vecStart = is3D ? i * seqLen * embedDim : i * embedDim;
							const vecEnd = vecStart + embedDim;
							const vector = tensor.data.slice(vecStart, vecEnd);

							let sumSq = 0;
							for (let j = 0; j < embedDim; j++) sumSq += vector[j] * vector[j];
							const norm = Math.sqrt(sumSq) || 1;

							const normVec = new Array(embedDim);
							for (let j = 0; j < embedDim; j++) {
								const val = Number(vector[j]);
								normVec[j] = (isNaN(val) ? 0 : val) / norm;
							}
							embeddings.push(normVec);
						}

						ws?.send(
							JSON.stringify({
								type: 'result',
								request_id: payload.request_id,
								embedding: embeddings
							})
						);
						echoEdgeEmbedState.set('ready');
					}
				} catch (e: any) {
					console.error('Erreur traitement embedding WebGPU:', e);
					echoEdgeEmbedState.set('ready');
				}
			};

			ws.onerror = (e) => {
				console.warn('Erreur WebSocket Edge Embed:', e);
			};

			ws.onclose = () => {
				ws = null;
			};
		} catch (err: any) {
			if (destroyed) return;
			console.warn('Échec initialisation Edge Embedding WebGPU:', err);
			echoEdgeEmbedReason.set(err?.message || 'Échec initialisation WebGPU');
			echoEdgeEmbedState.set('incompatible');
		}
	});

	onDestroy(() => {
		destroyed = true;
		if (typeof window !== 'undefined' && mode === 'badge') {
			window.removeEventListener('click', handleClickOutside);
		}
		if (typeof document !== 'undefined' && mode === 'bridge') {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
		}
		if (ws) {
			try {
				ws.close();
			} catch (e) {}
			ws = null;
		}
	});

	$: state = $echoEdgeEmbedState;
	$: progress = $echoEdgeEmbedProgress;
	$: reason = $echoEdgeEmbedReason;

	$: badgeBorder =
		state === 'active'
			? 'border-sky-500/60 bg-sky-500/10 text-sky-600 dark:text-sky-300 shadow-[0_0_8px_rgba(56,189,248,0.25)]'
			: state === 'ready'
				? 'border-sky-500/30 bg-gray-100/80 dark:bg-gray-800/80 text-gray-700 dark:text-gray-200 hover:border-sky-500/50'
				: state === 'downloading'
					? 'border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-300'
					: state === 'probing'
						? 'border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300'
						: state === 'incompatible'
							? 'border-red-500/30 bg-gray-100/80 dark:bg-gray-800/80 text-gray-500 dark:text-gray-400 hover:border-red-500/50'
							: 'border-gray-200/80 dark:border-gray-700/80 bg-gray-100/80 dark:bg-gray-800/80 text-gray-500 dark:text-gray-400';

	$: statusLabel =
		state === 'ready'
			? 'Prêt (VRAM fp16)'
			: state === 'active'
				? 'Vectorisation en cours...'
				: state === 'downloading'
					? `Téléchargement ONNX (${progress}%)`
					: state === 'probing'
						? 'Sonde matérielle WebGPU...'
						: state === 'incompatible'
							? `Indisponible${reason ? ` : ${reason}` : ''}`
							: 'En attente';
</script>

{#if mode === 'badge'}
	<div class="relative inline-flex items-center shrink-0" bind:this={containerRef}>
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-2 py-1 rounded-xl border text-xs font-mono transition shadow-xs select-none {badgeBorder}"
			on:click={() => (showPopover = !showPopover)}
			title={statusLabel}
			aria-label="État WebGPU Edge Embedding"
		>
			<!-- Point d'état -->
			<span class="relative flex items-center justify-center w-2 h-2 shrink-0">
				{#if state === 'active'}
					<span class="absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 animate-ping"></span>
					<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400"></span>
				{:else if state === 'ready'}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400"></span>
				{:else if state === 'downloading'}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400 animate-pulse"></span>
				{:else if state === 'probing'}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-amber-400 animate-pulse"></span>
				{:else if state === 'incompatible'}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-red-400/80"></span>
				{:else}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-gray-400"></span>
				{/if}
			</span>

			<!-- Icône puce GPU SVG compacte -->
			<svg
				class="w-3.5 h-3.5 shrink-0"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.75"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<rect x="6" y="6" width="12" height="12" rx="2" />
				<rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
				<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" />
			</svg>

			{#if state === 'downloading'}
				<span class="font-semibold">{progress}%</span>
			{:else}
				<span class="hidden sm:inline font-medium">WebGPU</span>
			{/if}
		</button>

		{#if showPopover}
			<div
				class="bottom-full right-0 mb-2 absolute w-72 p-3.5 rounded-xl shadow-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-xs z-50 animate-in fade-in zoom-in-95 duration-150"
			>
				<div class="flex items-center justify-between pb-2 mb-2.5 border-b border-gray-100 dark:border-gray-800">
					<span class="font-semibold text-gray-900 dark:text-white flex items-center gap-1.5">
						<span
							class="inline-block w-2 h-2 rounded-full {state === 'active' || state === 'ready' || state === 'downloading'
								? 'bg-sky-400'
								: state === 'probing'
									? 'bg-amber-400'
									: state === 'incompatible'
										? 'bg-red-400'
										: 'bg-gray-400'}"
						></span>
						Indexation locale WebGPU
					</span>
					<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-gray-500">
						{TARGET_DTYPE}
					</span>
				</div>

				<div class="space-y-2 font-mono text-[11px]">
					<div class="flex justify-between gap-2">
						<span class="text-gray-500 dark:text-gray-400 shrink-0">État :</span>
						<span class="text-right font-semibold text-gray-900 dark:text-gray-100 break-words">
							{statusLabel}
						</span>
					</div>

					{#if state === 'downloading'}
						<div class="w-full h-1.5 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
							<div
								class="h-full bg-sky-400 transition-all duration-200"
								style="width: {progress}%"
							></div>
						</div>
					{/if}

					<div class="pt-1.5 border-t border-gray-100 dark:border-gray-800">
						<div class="text-[10px] text-gray-400 dark:text-gray-500">Modèle ONNX :</div>
						<div class="text-[10px] text-gray-700 dark:text-gray-300 truncate" title={TARGET_REPO}>
							{TARGET_REPO}
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>
{/if}
