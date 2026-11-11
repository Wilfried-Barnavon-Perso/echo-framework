<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { getEchoCognitionSnapshot } from '$lib/apis/echo';
	import { echoCognitionActiveCount } from '$lib/stores/echo';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let chatId: string = '';

	let loading = true;
	let currentChatId = '';
	let activeTab: 'threads' | 'tools' = 'threads';
	let threads: Record<string, any[]> = {};
	let toolCalls: any[] = [];
	let eventSource: EventSource | null = null;

	$: if (chatId && chatId !== currentChatId) {
		currentChatId = chatId;
		initData(chatId);
	}

	const initData = async (cid: string) => {
		loading = true;
		await loadSnapshot(cid);
		loading = false;
		setupEventSource(cid);
	};

	const loadSnapshot = async (cid: string) => {
		const token = localStorage?.token;
		if (!token || !cid) return;
		try {
			const res = await getEchoCognitionSnapshot(token, cid);
			if (res) {
				threads = res.threads || {};
				toolCalls = res.tool_calls || [];
				echoCognitionActiveCount.set(Object.keys(threads).length);
			}
		} catch (e) {
			console.warn('Impossible de charger le snapshot cognition:', e);
		}
	};

	const setupEventSource = (cid: string) => {
		if (eventSource) {
			eventSource.close();
			eventSource = null;
		}
		const token = localStorage?.token;
		if (!token || !cid) return;

		try {
			const url = `/echo/v1/cognition/${encodeURIComponent(cid)}/stream?token=${encodeURIComponent(token)}`;
			eventSource = new EventSource(url);
			eventSource.onmessage = (event) => {
				try {
					const data = JSON.parse(event.data);
					if (data.threads) threads = data.threads;
					if (data.tool_calls) toolCalls = data.tool_calls;
					echoCognitionActiveCount.set(Object.keys(threads).length);
				} catch (e) {}
			};
			eventSource.onerror = () => {
				// Fermeture silencieuse en cas de déconnexion
				eventSource?.close();
				eventSource = null;
			};
		} catch (e) {}
	};

	onMount(() => {
		if (chatId) initData(chatId);
	});

	onDestroy(() => {
		if (eventSource) {
			eventSource.close();
			eventSource = null;
		}
	});

	const formatTimestamp = (ts: any): string => {
		if (!ts) return '';
		const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
		return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	};
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre de contrôle supérieure -->
	<div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850">
		<div class="flex items-center gap-2">
			<span class="font-bold text-sm text-gray-900 dark:text-white flex items-center gap-1.5">
				<svg class="w-4 h-4 text-purple-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
				</svg>
				Activité Cognitive
			</span>
			<span class="text-xs px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-500 font-mono font-medium">
				{Object.keys(threads).length} thread(s) • {toolCalls.length} outil(s)
			</span>
		</div>

		<div class="flex items-center gap-1 bg-gray-200 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium">
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'threads' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-400'}"
				on:click={() => (activeTab = 'threads')}
			>
				Fils Délibératifs
			</button>
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'tools' ? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-400'}"
				on:click={() => (activeTab = 'tools')}
			>
				Journal Outils
			</button>
			<button
				type="button"
				class="p-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 ml-1"
				title="Actualiser"
				on:click={() => loadSnapshot(chatId)}
			>
				<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Contenu défilant -->
	<div class="flex-1 overflow-y-auto p-4 space-y-4">
		{#if loading}
			<div class="flex items-center justify-center h-48">
				<Spinner className="w-6 h-6 text-purple-500" />
			</div>
		{:else if activeTab === 'threads'}
			{#if Object.keys(threads).length === 0}
				<div class="flex flex-col items-center justify-center h-48 text-gray-400 text-xs">
					<svg class="w-8 h-8 mb-2 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
						<circle cx="12" cy="12" r="10" />
						<path d="M12 16v-4M12 8h.01" />
					</svg>
					Aucun fil cognitif enregistré pour cette session.
				</div>
			{:else}
				{#each Object.entries(threads) as [subSid, steps]}
					<div class="border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden bg-gray-50/50 dark:bg-gray-850/50">
						<div class="px-3.5 py-2.5 bg-gray-100/80 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
							<div class="flex items-center gap-2">
								<span class="w-2 h-2 rounded-full bg-purple-500"></span>
								<span class="font-mono font-bold text-xs text-purple-600 dark:text-purple-400">
									{subSid}
								</span>
								{#if steps[0]?.role}
									<span class="text-xs px-2 py-0.5 rounded bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
										{steps[0].role}
									</span>
								{/if}
							</div>
							<span class="text-[11px] text-gray-400 font-mono">
								{steps.length} étape(s)
							</span>
						</div>

						<div class="p-3 space-y-3 font-mono text-xs">
							{#each steps as step}
								<div class="p-2.5 rounded-lg bg-white dark:bg-gray-900 border border-gray-200/60 dark:border-gray-800/60 space-y-1.5">
									<div class="flex justify-between items-center text-[11px] text-gray-500">
										<span class="font-semibold text-purple-500">Étape #{step.step_index}</span>
										<span>{formatTimestamp(step.updated_at)}</span>
									</div>
									{#if step.thought_signature}
										<div class="text-[10px] text-gray-400 bg-gray-50 dark:bg-gray-950 p-1 rounded border border-gray-100 dark:border-gray-850 truncate">
											Signature: {step.thought_signature}
										</div>
									{/if}
									<div class="text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-sans text-xs">
										{#if typeof step.content_json === 'object'}
											<pre class="font-mono text-[11px] overflow-x-auto p-1.5 bg-gray-50 dark:bg-gray-950 rounded">{JSON.stringify(step.content_json, null, 2)}</pre>
										{:else}
											{step.content_json}
										{/if}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/each}
			{/if}
		{:else}
			<!-- Onglet Journal Outils -->
			{#if toolCalls.length === 0}
				<div class="flex flex-col items-center justify-center h-48 text-gray-400 text-xs">
					<svg class="w-8 h-8 mb-2 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
						<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
					</svg>
					Aucun appel d'outil consigné pour cette session.
				</div>
			{:else}
				<div class="space-y-2">
					{#each toolCalls as call}
						<div class="p-3 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-850 font-mono text-xs space-y-1.5">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2">
									<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
									<span class="font-bold text-emerald-600 dark:text-emerald-400">
										{call.function_name}
									</span>
								</div>
								<span class="text-[11px] text-gray-400">{formatTimestamp(call.timestamp)}</span>
							</div>

							{#if call.call_id}
								<div class="text-[10px] text-gray-400 truncate">ID: {call.call_id}</div>
							{/if}

							<div class="p-2 bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-100 dark:border-gray-800 overflow-x-auto text-[11px]">
								<pre>{typeof call.args_json === 'object' ? JSON.stringify(call.args_json, null, 2) : call.args_json}</pre>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	</div>
</div>
