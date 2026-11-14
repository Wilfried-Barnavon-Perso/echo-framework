<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { postEchoSubagentMessage } from '$lib/apis/echo';
	import {
		acquireCognitionStream,
		echoCognitionSelectedSubSid,
		echoCognitionSnapshot,
		refreshCognitionSnapshot,
		type EchoCognitionStep
	} from '$lib/stores/echo';
	import { isSavedChatId } from '$lib/utils/chatId';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let chatId: string = '';

	let mounted = false;
	let boundChatId = '';
	let releaseStream: (() => void) | null = null;
	let initialLoading = false;
	let refreshing = false;
	let activeTab: 'threads' | 'tools' = 'threads';
	let composerText = '';
	let sending = false;
	let feedContainer: HTMLDivElement | null = null;
	let lastScrolledKey = '';

	const syncChatStream = async (cid: string) => {
		const normalized = cid && isSavedChatId(cid) ? cid : '';
		if (normalized === boundChatId) return;

		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = normalized;

		if (!normalized) {
			initialLoading = false;
			return;
		}

		initialLoading = !$echoCognitionSnapshot || $echoCognitionSnapshot.chat_id !== normalized;
		releaseStream = acquireCognitionStream(normalized);
		await refreshCognitionSnapshot(normalized);
		initialLoading = false;
	};

	onMount(() => {
		mounted = true;
		void syncChatStream(chatId);
	});

	$: if (mounted) {
		void syncChatStream(chatId);
	}

	onDestroy(() => {
		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = '';
	});

	const handleManualRefresh = async () => {
		if (!chatId || !isSavedChatId(chatId) || refreshing) return;
		refreshing = true;
		try {
			await refreshCognitionSnapshot(chatId);
		} finally {
			refreshing = false;
		}
	};

	$: snapshot = $echoCognitionSnapshot;
	$: threads = snapshot?.threads ?? {};
	$: threadMeta = snapshot?.thread_meta ?? {};
	$: queuedMap = snapshot?.queued_messages ?? {};
	$: toolCalls = snapshot?.tool_calls ?? [];

	const getThreadUpdatedAt = (sid: string): number => {
		const metaTs = Number(threadMeta[sid]?.updated_at ?? 0) || 0;
		const steps = threads[sid] ?? [];
		const lastStepTs = steps.length > 0 ? Number(steps[steps.length - 1]?.updated_at ?? 0) || 0 : 0;
		return Math.max(metaTs, lastStepTs);
	};

	$: sortedSubSids = Array.from(new Set([...Object.keys(threads), ...Object.keys(threadMeta)])).sort(
		(a, b) => {
			const aRunning = threadMeta[a]?.status === 'running' ? 1 : 0;
			const bRunning = threadMeta[b]?.status === 'running' ? 1 : 0;
			if (aRunning !== bRunning) return bRunning - aRunning;
			return getThreadUpdatedAt(b) - getThreadUpdatedAt(a);
		}
	);

	$: if (chatId && sortedSubSids.length > 0) {
		const currentSel = $echoCognitionSelectedSubSid[chatId];
		if (!currentSel || !sortedSubSids.includes(currentSel)) {
			echoCognitionSelectedSubSid.update((curr) => ({
				...curr,
				[chatId]: sortedSubSids[0]
			}));
		}
	}

	$: selectedSubSid = chatId ? ($echoCognitionSelectedSubSid[chatId] ?? '') : '';
	$: selectedSteps = selectedSubSid ? (threads[selectedSubSid] ?? []) : [];
	$: selectedMeta = selectedSubSid ? (threadMeta[selectedSubSid] ?? {}) : {};
	$: selectedQueue = selectedSubSid ? (queuedMap[selectedSubSid] ?? []) : [];
	$: selectedRoleLabel =
		selectedMeta?.role ||
		selectedSteps.find((s) => s.role_id)?.role_id ||
		selectedSubSid ||
		'Sous-agent';
	$: selectedIsRunning = selectedMeta?.status === 'running';

	const getSubSidLabel = (sid: string): string => {
		const meta = threadMeta[sid];
		const steps = threads[sid] ?? [];
		const role = meta?.role || steps.find((s) => s.role_id)?.role_id || sid;
		const statusLabel = meta?.status === 'running' ? 'En cours' : 'En attente';
		const queueLen = (queuedMap[sid] ?? []).length;
		const queueBadge = queueLen > 0 ? ` (+${queueLen} en attente)` : '';
		return `${role} — ${statusLabel}${queueBadge}`;
	};

	const onSelectSubSid = (e: Event) => {
		const target = e.currentTarget as HTMLSelectElement;
		if (!chatId || !target.value) return;
		echoCognitionSelectedSubSid.update((curr) => ({
			...curr,
			[chatId]: target.value
		}));
	};

	interface ParsedStepView {
		thoughtTexts: string[];
		visibleTexts: string[];
		toolCalls: { name: string; args?: any }[];
		toolResponses: { name: string; response?: any }[];
		fallbackRaw: string;
	}

	const parseStepParts = (step: EchoCognitionStep): ParsedStepView => {
		const result: ParsedStepView = {
			thoughtTexts: [],
			visibleTexts: [],
			toolCalls: [],
			toolResponses: [],
			fallbackRaw: ''
		};

		const content = step.content;
		if (Array.isArray(content)) {
			for (const part of content) {
				if (!part || typeof part !== 'object') {
					if (typeof part === 'string' && part.trim()) {
						result.visibleTexts.push(part);
					}
					continue;
				}
				if (typeof part.text === 'string' && part.text.trim()) {
					if (part.thought) {
						result.thoughtTexts.push(part.text.trim());
					} else {
						result.visibleTexts.push(part.text);
					}
				}
				if (part.functionCall && typeof part.functionCall === 'object') {
					result.toolCalls.push({
						name: part.functionCall.name || 'outil',
						args: part.functionCall.args
					});
				}
				if (part.functionResponse && typeof part.functionResponse === 'object') {
					result.toolResponses.push({
						name: part.functionResponse.name || 'outil',
						response: part.functionResponse.response
					});
				}
			}
		} else if (typeof content === 'string') {
			result.visibleTexts.push(content);
		} else if (content && typeof content === 'object') {
			result.fallbackRaw = JSON.stringify(content, null, 2);
		}

		return result;
	};

	const formatTimestamp = (ts: any): string => {
		if (!ts) return '';
		const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
		if (Number.isNaN(d.getTime())) return '';
		return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	};

	const scrollToBottom = async () => {
		await tick();
		if (feedContainer) {
			feedContainer.scrollTop = feedContainer.scrollHeight;
		}
	};

	$: {
		const scrollKey = `${selectedSubSid}:${selectedSteps.length}:${selectedQueue.length}`;
		if (activeTab === 'threads' && selectedSubSid && scrollKey !== lastScrolledKey) {
			lastScrolledKey = scrollKey;
			void scrollToBottom();
		}
	}

	const sendMessage = async () => {
		const text = composerText.trim();
		if (!text || !chatId || !isSavedChatId(chatId) || !selectedSubSid || sending) return;
		const token = localStorage?.token;
		if (!token) {
			toast.error('Session expirée : veuillez vous reconnecter.');
			return;
		}

		sending = true;
		const previousText = composerText;
		composerText = '';
		try {
			const res = await postEchoSubagentMessage(token, chatId, selectedSubSid, text);
			if (res.status === 'queued') {
				const count = res.queue_length ?? 1;
				toast.info(`Message mis en file pour le prochain tour (${count} en attente).`);
			} else {
				toast.success('Message envoyé au sous-agent.');
			}
			await refreshCognitionSnapshot(chatId);
			await scrollToBottom();
		} catch (e: any) {
			composerText = previousText;
			toast.error(e?.message || "Impossible d'envoyer le message au sous-agent.");
		} finally {
			sending = false;
		}
	};

	const handleComposerKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			void sendMessage();
		}
	};
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre de contrôle supérieure -->
	<div class="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0">
		<div class="flex items-center gap-2 min-w-0">
			<span class="font-semibold text-sm text-gray-900 dark:text-white flex items-center gap-1.5 truncate">
				<svg class="w-4 h-4 text-indigo-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="12" cy="6" r="3" />
					<circle cx="6" cy="18" r="3" />
					<circle cx="18" cy="18" r="3" />
					<path d="M10.5 8.5L7.5 15.5M13.5 8.5l3 7" />
				</svg>
				Sous-agents
			</span>
			<span class="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-mono font-medium shrink-0">
				{sortedSubSids.length} • {toolCalls.length} outil(s)
			</span>
		</div>

		<div class="flex items-center gap-1 bg-gray-200/80 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium shrink-0">
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'threads'
					? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white'
					: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
				on:click={() => (activeTab = 'threads')}
			>
				Conversations
			</button>
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'tools'
					? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white'
					: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
				on:click={() => (activeTab = 'tools')}
			>
				Appels d'outils
			</button>
			<button
				type="button"
				class="p-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 ml-0.5 disabled:opacity-40"
				title="Actualiser"
				disabled={!isSavedChatId(chatId) || refreshing}
				on:click={handleManualRefresh}
			>
				<svg
					class="w-3.5 h-3.5 {refreshing ? 'animate-spin' : ''}"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
				>
					<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
				</svg>
			</button>
		</div>
	</div>

	{#if initialLoading}
		<div class="flex-1 flex items-center justify-center">
			<Spinner className="w-6 h-6 text-indigo-500" />
		</div>
	{:else if activeTab === 'threads'}
		{#if !isSavedChatId(chatId)}
			<div class="flex-1 flex flex-col items-center justify-center p-6 text-center text-gray-400 text-xs">
				Démarrez une conversation pour consulter et piloter les sous-agents de la session.
			</div>
		{:else if sortedSubSids.length === 0}
			<div class="flex-1 flex flex-col items-center justify-center p-6 text-center text-gray-400 text-xs">
				<svg class="w-8 h-8 mb-2 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
					<circle cx="12" cy="12" r="10" />
					<path d="M12 16v-4M12 8h.01" />
				</svg>
				Aucun sous-agent actif ou enregistré pour cette conversation.
			</div>
		{:else}
			<!-- Sélecteur de sous-agent -->
			<div class="px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-850/60 flex items-center gap-2.5 shrink-0">
				<span class="relative flex items-center justify-center w-2.5 h-2.5 shrink-0">
					{#if selectedIsRunning}
						<span class="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping bg-emerald-500"></span>
						<span class="relative inline-flex rounded-full w-2 h-2 bg-emerald-500"></span>
					{:else}
						<span class="relative inline-flex rounded-full w-2 h-2 bg-indigo-400"></span>
					{/if}
				</span>

				<select
					aria-label="Sélectionner un sous-agent"
					value={selectedSubSid}
					on:change={onSelectSubSid}
					class="flex-1 min-w-0 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-xs font-medium text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
				>
					{#each sortedSubSids as sid (sid)}
						<option value={sid}>{getSubSidLabel(sid)}</option>
					{/each}
				</select>

				{#if selectedQueue.length > 0}
					<span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 font-medium shrink-0">
						+{selectedQueue.length} en attente
					</span>
				{/if}
			</div>

			<!-- Fil des échanges -->
			<div bind:this={feedContainer} class="flex-1 overflow-y-auto p-4 space-y-3">
				{#if selectedSteps.length === 0}
					<div class="flex items-center justify-center h-32 text-xs text-gray-400">
						Démarrage du sous-agent en cours…
					</div>
				{:else}
					{#each selectedSteps as step (`${selectedSubSid}:${step.step_index}`)}
						{@const parsed = parseStepParts(step)}
						{#if step.role === 'user'}
							{#if step.origin === 'tool' && !step.clean_text && parsed.toolResponses.length > 0}
								<!-- Retours d'outils discrets -->
								<div class="flex flex-wrap items-center gap-1.5 px-1">
									{#each parsed.toolResponses as tr}
										<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border border-gray-200/70 dark:border-gray-700/70">
											<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
											Retour : {tr.name}
										</span>
									{/each}
								</div>
							{:else if step.origin === 'human'}
								<!-- Message humain -->
								<div class="rounded-xl p-3 bg-emerald-50/70 dark:bg-emerald-950/25 border border-emerald-200/80 dark:border-emerald-800/40 space-y-1.5">
									<div class="flex items-center justify-between gap-2">
										<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
											Vous
										</span>
										<span class="text-[11px] text-gray-400 font-mono">
											{formatTimestamp(step.updated_at)}
										</span>
									</div>
									<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
										{step.clean_text ?? parsed.visibleTexts.join('\n')}
									</div>
								</div>
							{:else}
								<!-- Directive orchestrateur -->
								<div class="rounded-xl p-3 bg-indigo-50/70 dark:bg-indigo-950/25 border border-indigo-200/80 dark:border-indigo-800/40 space-y-1.5">
									<div class="flex items-center justify-between gap-2">
										<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-700 dark:text-indigo-300">
											Orchestrateur
										</span>
										<span class="text-[11px] text-gray-400 font-mono">
											{formatTimestamp(step.updated_at)}
										</span>
									</div>
									{#if step.clean_text || parsed.visibleTexts.length > 0}
										<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
											{step.clean_text ?? parsed.visibleTexts.join('\n')}
										</div>
									{/if}
									{#if parsed.toolResponses.length > 0}
										<div class="flex flex-wrap gap-1.5 pt-1">
											{#each parsed.toolResponses as tr}
												<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-white/80 dark:bg-gray-900/70 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700">
													Retour : {tr.name}
												</span>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						{:else}
							<!-- Réponse du sous-agent (model) -->
							<div class="rounded-xl p-3 bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 space-y-2">
								<div class="flex items-center justify-between gap-2">
									<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200">
										{selectedRoleLabel}
									</span>
									<span class="text-[11px] text-gray-400 font-mono">
										{formatTimestamp(step.updated_at)}
									</span>
								</div>

								{#if parsed.thoughtTexts.length > 0}
									<details class="rounded-lg bg-white/70 dark:bg-gray-900/60 border border-gray-200/70 dark:border-gray-800 text-[11px]">
										<summary class="cursor-pointer select-none px-2.5 py-1.5 text-gray-500 dark:text-gray-400 font-medium hover:text-gray-700 dark:hover:text-gray-200">
											Réflexion interne
										</summary>
										<div class="px-2.5 pb-2 pt-1 text-gray-600 dark:text-gray-300 whitespace-pre-wrap leading-relaxed border-t border-gray-100 dark:border-gray-800">
											{parsed.thoughtTexts.join('\n\n')}
										</div>
									</details>
								{/if}

								{#if parsed.visibleTexts.length > 0}
									<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
										{parsed.visibleTexts.join('\n')}
									</div>
								{:else if parsed.fallbackRaw}
									<pre class="font-mono text-[11px] overflow-x-auto p-2 bg-white dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-800">{parsed.fallbackRaw}</pre>
								{/if}

								{#if parsed.toolCalls.length > 0}
									<div class="flex flex-wrap gap-1.5 pt-0.5">
										{#each parsed.toolCalls as tc}
											<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-700 dark:text-purple-300 border border-purple-500/20 font-mono">
												<span class="w-1.5 h-1.5 rounded-full bg-purple-500"></span>
												{tc.name}
											</span>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					{/each}
				{/if}
			</div>

			<!-- Bandeau file d'attente FIFO -->
			{#if selectedQueue.length > 0}
				<div class="mx-3 mb-2 rounded-xl border border-amber-300/80 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 space-y-1.5 shrink-0">
					<div class="flex items-center justify-between text-[11px] font-semibold text-amber-800 dark:text-amber-300">
						<span>File d'attente ({selectedQueue.length}) — envoi au prochain tour</span>
					</div>
					<div class="space-y-1 max-h-24 overflow-y-auto">
						{#each selectedQueue as qMsg, idx (qMsg.id ?? idx)}
							<div class="text-xs text-amber-900 dark:text-amber-100 bg-white/70 dark:bg-gray-900/50 rounded-lg px-2 py-1 border border-amber-200/70 dark:border-amber-800/40 whitespace-pre-wrap">
								{qMsg.content}
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Compositeur interactif -->
			<div class="p-3 border-t border-gray-200 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-850/70 shrink-0">
				<div class="flex items-end gap-2">
					<textarea
						bind:value={composerText}
						on:keydown={handleComposerKeydown}
						rows="2"
						disabled={sending}
						placeholder={selectedIsRunning
							? 'Envoyer une consigne (mise en file pour le prochain tour)...'
							: 'Envoyer une consigne directe au sous-agent...'}
						class="flex-1 resize-none rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-xs text-gray-800 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-50"
					></textarea>
					<button
						type="button"
						disabled={sending || !composerText.trim()}
						on:click={sendMessage}
						class="px-3 py-2 rounded-xl text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white transition disabled:opacity-40 disabled:cursor-not-allowed shrink-0 flex items-center gap-1.5"
					>
						{#if sending}
							<Spinner className="w-3.5 h-3.5" />
						{:else}
							<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
							</svg>
						{/if}
						Envoyer
					</button>
				</div>
				<div class="mt-1 text-[10px] text-gray-400">
					Entrée pour envoyer · Maj+Entrée pour nouvelle ligne
				</div>
			</div>
		{/if}
	{:else}
		<!-- Onglet Appels d'outils -->
		<div class="flex-1 overflow-y-auto p-4">
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
						{@const rawArgs = call.args ?? call.args_json}
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
								<div class="text-[10px] text-gray-400 truncate">Réf. : {call.call_id}</div>
							{/if}

							{#if rawArgs !== undefined && rawArgs !== null}
								<div class="p-2 bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-100 dark:border-gray-800 overflow-x-auto text-[11px]">
									<pre>{typeof rawArgs === 'object' ? JSON.stringify(rawArgs, null, 2) : rawArgs}</pre>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>
