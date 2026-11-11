<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { echoContextGauge, type EchoContextGaugePayload } from '$lib/stores/echo';
	import { getEchoWmChatSnapshot } from '$lib/apis/echo';

	export let chatId: string = '';

	let showPopover = false;
	let containerRef: HTMLDivElement | null = null;
	let currentChatId = '';

	const DEFAULT_MAX_TOKENS = 1048576;

	$: if (chatId && chatId !== currentChatId) {
		currentChatId = chatId;
		refreshStats(chatId);
	}

	const refreshStats = async (cid: string) => {
		const token = localStorage?.token;
		if (!token || !cid) return;
		try {
			const res = await getEchoWmChatSnapshot(token, cid);
			if (res && res.context_stats) {
				echoContextGauge.set(res.context_stats);
			}
		} catch (e) {
			// Silencieux si pas encore de session
		}
	};

	const handleCustomEvent = (e: Event) => {
		const custom = e as CustomEvent<EchoContextGaugePayload>;
		if (custom && custom.detail) {
			echoContextGauge.set(custom.detail);
		}
	};

	const handleClickOutside = (e: MouseEvent) => {
		if (showPopover && containerRef && !containerRef.contains(e.target as Node)) {
			showPopover = false;
		}
	};

	onMount(() => {
		window.addEventListener('echo:context_gauge', handleCustomEvent);
		window.addEventListener('click', handleClickOutside);
		if (chatId) {
			refreshStats(chatId);
		}
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('echo:context_gauge', handleCustomEvent);
			window.removeEventListener('click', handleClickOutside);
		}
	});

	$: stats = $echoContextGauge;
	$: maxTokens = stats?.max_tokens || DEFAULT_MAX_TOKENS;
	$: totalTokens =
		stats?.total_tokens ??
		((stats?.prompt_tokens ?? stats?.promptTokenCount ?? 0) +
			(stats?.generation_tokens ?? stats?.candidatesTokenCount ?? 0));
	$: cachedTokens = stats?.cached_tokens ?? stats?.cachedContentTokenCount ?? 0;
	$: promptTokens = stats?.prompt_tokens ?? stats?.promptTokenCount ?? 0;
	$: genTokens = stats?.generation_tokens ?? stats?.candidatesTokenCount ?? 0;
	$: deltaTokens = stats?.delta_tokens ?? stats?.delta_capacity ?? 0;
	$: compactions = stats?.compaction_count ?? 0;
	$: compactedTurns = stats?.compacted_turns ?? 0;
	$: isPending = stats?.is_pending ?? false;

	$: pct = stats?.percentage !== undefined
		? Math.min(100, Math.max(0, stats.percentage))
		: Math.min(100, Math.max(0, (totalTokens / maxTokens) * 100));

	$: pctFormatted = pct.toFixed(1);

	$: colorClass =
		pct > 80
			? 'bg-red-500 text-red-500'
			: pct > 50
				? 'bg-amber-500 text-amber-500'
				: 'bg-emerald-500 text-emerald-500';

	$: badgeBorder =
		pct > 80
			? 'border-red-500/40 hover:border-red-500'
			: pct > 50
				? 'border-amber-500/40 hover:border-amber-500'
				: 'border-emerald-500/30 hover:border-emerald-500/60';

	const formatNumber = (num: number): string => {
		if (num >= 1_000_000) return (num / 1_000_000).toFixed(2) + 'M';
		if (num >= 1_000) return (num / 1_000).toFixed(1) + 'k';
		return num.toLocaleString();
	};
</script>

<div class="relative inline-flex items-center" bind:this={containerRef}>
	<button
		type="button"
		class="flex items-center gap-2 px-2.5 py-1 text-xs font-mono font-medium rounded-lg border bg-gray-50 dark:bg-gray-850 hover:bg-gray-100 dark:hover:bg-gray-800 transition shadow-sm {badgeBorder}"
		on:click={() => (showPopover = !showPopover)}
		title="Jauge Contextuelle ECHO"
	>
		<!-- Indicateur couleur & pulsation -->
		<div class="relative flex items-center justify-center w-2.5 h-2.5">
			{#if isPending}
				<span
					class="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping {colorClass}"
				></span>
			{/if}
			<span class="relative inline-flex rounded-full w-2 h-2 {colorClass}"></span>
		</div>

		<!-- Mini barre de progression -->
		<div class="w-12 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
			<div
				class="h-full transition-all duration-500 {colorClass.split(' ')[0]}"
				style="width: {pct}%"
			></div>
		</div>

		<span class="text-gray-700 dark:text-gray-200 font-semibold">{pctFormatted}%</span>
		<span class="text-gray-400 dark:text-gray-500 hidden sm:inline"
			>({formatNumber(totalTokens)})</span
		>
	</button>

	<!-- Popover détaillé -->
	{#if showPopover}
		<div
			class="absolute right-0 top-full mt-2 w-80 p-4 rounded-xl shadow-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-xs z-50 animate-in fade-in zoom-in-95 duration-150"
		>
			<div class="flex items-center justify-between pb-2 mb-3 border-b border-gray-100 dark:border-gray-800">
				<div class="flex items-center gap-1.5 font-semibold text-gray-900 dark:text-white">
					<svg class="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
						<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
					</svg>
					<span>Jauge Contextuelle & Quotas</span>
				</div>
				<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-gray-500">
					ECHO V6
				</span>
			</div>

			<!-- Jauge Principale -->
			<div class="space-y-1 mb-4">
				<div class="flex justify-between font-mono text-gray-600 dark:text-gray-400">
					<span>Total Tokens :</span>
					<span class="font-bold text-gray-900 dark:text-white">
						{totalTokens.toLocaleString()} / {formatNumber(maxTokens)}
					</span>
				</div>
				<div class="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
					<div
						class="h-full transition-all duration-300 {colorClass.split(' ')[0]}"
						style="width: {pct}%"
					></div>
				</div>
			</div>

			<!-- Décomposition Tokens -->
			<div class="space-y-1.5 py-2 border-t border-b border-gray-100 dark:border-gray-800 font-mono">
				<div class="flex justify-between text-gray-600 dark:text-gray-400">
					<span>Tokens en Cache :</span>
					<span class="text-blue-500 font-semibold">{cachedTokens.toLocaleString()}</span>
				</div>
				<div class="flex justify-between text-gray-600 dark:text-gray-400">
					<span>Prompt Actif :</span>
					<span class="text-emerald-500 font-semibold">{promptTokens.toLocaleString()}</span>
				</div>
				<div class="flex justify-between text-gray-600 dark:text-gray-400">
					<span>Génération :</span>
					<span class="text-purple-500 font-semibold">{genTokens.toLocaleString()}</span>
				</div>
				{#if deltaTokens !== 0}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>Δ Capacité :</span>
						<span class="text-amber-500 font-semibold">{deltaTokens > 0 ? '+' : ''}{deltaTokens.toLocaleString()}</span>
					</div>
				{/if}
				{#if compactions > 0}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>Compactions :</span>
						<span class="text-cyan-500 font-semibold">{compactions} ({compactedTurns} tours)</span>
					</div>
				{/if}
			</div>

			<!-- Quotas Gemini -->
			{#if stats?.quotas}
				<div class="mt-3 space-y-1.5">
					<div class="font-semibold text-gray-700 dark:text-gray-300">Quotas Fournisseur :</div>
					{#if stats.quotas.five_hour_pct !== undefined}
						<div class="flex items-center justify-between text-gray-500">
							<span>Fenêtre 5h :</span>
							<span class="font-mono font-semibold">{stats.quotas.five_hour_pct}%</span>
						</div>
					{/if}
					{#if stats.quotas.weekly_pct !== undefined}
						<div class="flex items-center justify-between text-gray-500">
							<span>Hebdomadaire :</span>
							<span class="font-mono font-semibold">{stats.quotas.weekly_pct}%</span>
						</div>
					{/if}
					{#if stats.quotas.rpd_usage !== undefined}
						<div class="flex items-center justify-between text-gray-500">
							<span>RPD :</span>
							<span class="font-mono font-semibold">{stats.quotas.rpd_usage} / {stats.quotas.rpd_limit ?? '∞'}</span>
						</div>
					{/if}
				</div>
			{/if}

			<div class="mt-3 pt-2 text-[10px] text-center text-gray-400 dark:text-gray-500 border-t border-gray-100 dark:border-gray-800">
				Fenêtre maximale standard : 1 048 576 tokens
			</div>
		</div>
	{/if}
</div>
