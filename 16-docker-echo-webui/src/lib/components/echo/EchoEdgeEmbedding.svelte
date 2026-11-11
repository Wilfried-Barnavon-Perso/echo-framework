<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { mobile } from '$lib/stores';
	import { echoEdgeEmbedState } from '$lib/stores/echo';

	const TARGET_REPO = 'onnx-community/harrier-oss-v1-0.6b-ONNX';
	const TARGET_DTYPE = 'fp16';
	const MIN_BUFFER_BYTES = 134217728; // 128 Mo

	let ws: WebSocket | null = null;
	let downloadPercent = 0;
	let showDownloadingHUD = false;
	let clientId = '';

	const probeWebGPU = async (): Promise<{ compatible: boolean; reason?: string }> => {
		if (typeof navigator === 'undefined' || !navigator.gpu) {
			return { compatible: false, reason: 'WebGPU non exposé par le navigateur' };
		}
		let adapter: any = null;
		try {
			adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
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

	onMount(async () => {
		if ($mobile) {
			echoEdgeEmbedState.set('incompatible');
			return;
		}

		echoEdgeEmbedState.set('probing');
		clientId = crypto.randomUUID();

		const probe = await probeWebGPU();
		if (!probe.compatible) {
			echoEdgeEmbedState.set('incompatible');
			// Notifier l'embedding worker via WS court
			try {
				const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
				const quickWs = new WebSocket(`${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`);
				quickWs.onopen = () => {
					quickWs.send(JSON.stringify({ type: 'incompatible', reason: probe.reason }));
					setTimeout(() => quickWs.close(), 100);
				};
			} catch (e) {}
			return;
		}

		echoEdgeEmbedState.set('downloading');
		showDownloadingHUD = true;

		try {
			const tfUrl = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js';
			const transformers = await import(/* @vite-ignore */ tfUrl);
			transformers.env.allowLocalModels = false;

			const tokenizer = await transformers.AutoTokenizer.from_pretrained(TARGET_REPO);
			const model = await transformers.AutoModel.from_pretrained(TARGET_REPO, {
				device: 'webgpu',
				dtype: TARGET_DTYPE,
				progress_callback: (x: any) => {
					if (x.status === 'downloading' || x.status === 'progress') {
						downloadPercent = x.total ? Math.round((x.loaded / x.total) * 100) : 0;
					}
				}
			});

			showDownloadingHUD = false;
			echoEdgeEmbedState.set('ready');

			const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
			const wsUrl = `${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`;
			ws = new WebSocket(wsUrl);

			ws.onopen = () => {
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

						const tensor = outputs.sentence_embedding || outputs.last_hidden_state || Object.values(outputs)[0];
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
		} catch (err: any) {
			console.warn('Échec initialisation Edge Embedding WebGPU:', err);
			showDownloadingHUD = false;
			echoEdgeEmbedState.set('incompatible');
		}
	});

	onDestroy(() => {
		if (typeof document !== 'undefined') {
			document.removeEventListener('visibilitychange', handleVisibilityChange);
		}
		if (ws) {
			try {
				ws.close();
			} catch (e) {}
			ws = null;
		}
	});
</script>

{#if showDownloadingHUD}
	<div
		class="fixed bottom-4 right-4 z-50 flex items-center gap-3 px-3.5 py-2.5 rounded-xl shadow-lg border border-sky-500/30 bg-slate-900/90 backdrop-blur text-sky-400 text-xs font-mono animate-in fade-in slide-in-from-bottom-2 duration-300"
	>
		<div class="relative flex items-center justify-center w-3 h-3">
			<span class="absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 animate-ping"></span>
			<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-500"></span>
		</div>
		<div class="flex flex-col gap-1">
			<div class="flex justify-between items-center gap-4">
				<span class="font-semibold text-slate-200">ECHO WebGPU Harri-OSS</span>
				<span>{downloadPercent}%</span>
			</div>
			<div class="w-36 h-1.5 bg-slate-800 rounded-full overflow-hidden">
				<div
					class="h-full bg-sky-400 transition-all duration-200"
					style="width: {downloadPercent}%"
				></div>
			</div>
		</div>
	</div>
{/if}
