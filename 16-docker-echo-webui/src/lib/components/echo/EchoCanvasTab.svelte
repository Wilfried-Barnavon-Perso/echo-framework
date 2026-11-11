<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { echoCanvasFrames, type EchoCanvasFrame } from '$lib/stores/echo';
	import { postEchoWmRpc } from '$lib/apis/echo';

	export let chatId: string = '';

	let activeFrameId = '';
	let iframeRef: HTMLIFrameElement | null = null;

	$: frames = ($echoCanvasFrames[chatId] || []) as EchoCanvasFrame[];
	$: if (frames.length > 0 && (!activeFrameId || !frames.some((f) => f.id === activeFrameId))) {
		activeFrameId = frames[frames.length - 1].id;
	}

	$: activeFrame = frames.find((f) => f.id === activeFrameId) || frames[0] || null;

	const handleMessage = async (e: MessageEvent) => {
		if (!e.data || typeof e.data !== 'object') return;
		// Relais RPC venant de l'application Canvas vers le backend ECHO
		if (e.data.type === 'echo:rpc' || e.data.type === 'wm_rpc') {
			const token = localStorage?.token;
			if (!token) return;
			try {
				const response = await postEchoWmRpc(token, {
					chat_id: chatId,
					frame_id: activeFrameId,
					...e.data.payload
				});
				if (iframeRef && iframeRef.contentWindow) {
					iframeRef.contentWindow.postMessage(
						{ type: 'echo:rpc_response', request_id: e.data.request_id, response },
						'*'
					);
				}
			} catch (err: any) {
				if (iframeRef && iframeRef.contentWindow) {
					iframeRef.contentWindow.postMessage(
						{ type: 'echo:rpc_error', request_id: e.data.request_id, error: err.message },
						'*'
					);
				}
			}
		}
	};

	const broadcastTheme = () => {
		if (!iframeRef || !iframeRef.contentWindow) return;
		const isDark = document.documentElement.classList.contains('dark');
		iframeRef.contentWindow.postMessage({ type: 'theme-update', dark: isDark }, '*');
	};

	let themeObserver: MutationObserver | null = null;

	onMount(() => {
		window.addEventListener('message', handleMessage);
		themeObserver = new MutationObserver(() => {
			broadcastTheme();
		});
		themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('message', handleMessage);
		}
		if (themeObserver) {
			themeObserver.disconnect();
			themeObserver = null;
		}
	});

	const handleIframeLoad = () => {
		broadcastTheme();
	};
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre supérieure des frames Canvas -->
	<div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850">
		<div class="flex items-center gap-2 overflow-x-auto">
			<span class="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-1.5 flex-shrink-0">
				<svg class="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
					<line x1="8" y1="21" x2="16" y2="21" />
					<line x1="12" y1="17" x2="12" y2="21" />
				</svg>
				Canvas & Apps
			</span>

			{#if frames.length > 1}
				<div class="flex items-center gap-1 bg-gray-200 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium ml-2">
					{#each frames as f}
						<button
							type="button"
							class="px-2 py-0.5 rounded transition text-xs font-mono {activeFrameId === f.id ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white font-semibold' : 'text-gray-600 dark:text-gray-400'}"
							on:click={() => (activeFrameId = f.id)}
						>
							{f.title || f.id.slice(0, 8)}
						</button>
					{/each}
				</div>
			{/if}
		</div>

		{#if activeFrame}
			<div class="flex items-center gap-2">
				<span class="text-[11px] font-mono text-gray-400">
					{activeFrame.title || activeFrame.id}
				</span>
				<button
					type="button"
					class="p-1 rounded text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
					title="Recharger le Canvas"
					on:click={() => {
						if (iframeRef) {
							if (activeFrame?.src) iframeRef.src = activeFrame.src;
							else if (activeFrame?.html) iframeRef.srcdoc = activeFrame.html;
						}
					}}
				>
					<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
					</svg>
				</button>
			</div>
		{/if}
	</div>

	<!-- Zone de rendu iframe sandboxée -->
	<div class="flex-1 w-full h-full relative bg-gray-100 dark:bg-gray-950">
		{#if !activeFrame}
			<div class="flex flex-col items-center justify-center h-full text-gray-400 text-xs">
				<svg class="w-12 h-12 mb-2 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
					<rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
					<line x1="8" y1="21" x2="16" y2="21" />
					<line x1="12" y1="17" x2="12" y2="21" />
				</svg>
				Aucune interface active émise par l'assistant ou le Window Manager.
			</div>
		{:else}
			<iframe
				bind:this={iframeRef}
				title={activeFrame.title || 'ECHO Canvas'}
				src={activeFrame.src || undefined}
				srcdoc={activeFrame.src ? undefined : activeFrame.html || ''}
				sandbox="allow-scripts allow-downloads allow-forms allow-same-origin"
				class="w-full h-full border-none"
				on:load={handleIframeLoad}
			></iframe>
		{/if}
	</div>
</div>
