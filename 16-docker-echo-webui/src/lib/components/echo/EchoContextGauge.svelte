<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { computeClampedPopoverStyle, echoContextGauge } from '$lib/stores/echo';
	import { getEchoToken, getEchoWmChatSnapshot } from '$lib/apis/echo';

	export let chatId: string = '';

	let showPopover = false;
	let popoverStyle = '';
	let containerRef: HTMLDivElement | null = null;
	let mounted = false;
	let currentChatId = '__init__';
	let isRefreshing = false;
	let refreshSeq = 0;
	let clickTimer: ReturnType<typeof setTimeout> | null = null;

	const DEFAULT_MAX_TOKENS = 1048576;
	const RING_RADIUS = 7;
	const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

	const refreshStats = async (cid: string, forceRefresh: boolean = false) => {
		const token = getEchoToken();
		if (!token) return;
		const seq = ++refreshSeq;
		if (forceRefresh) {
			isRefreshing = true;
		}
		try {
			const res = await getEchoWmChatSnapshot(token, cid || '_new', forceRefresh);
			if (seq !== refreshSeq || cid !== currentChatId) return;
			if (res && res.context_stats) {
				echoContextGauge.set(res.context_stats);
			} else if (!cid || cid === '_new') {
				echoContextGauge.set({});
			}
		} catch (e) {
			// Silencieux si pas encore de session
		} finally {
			if (forceRefresh && seq === refreshSeq) {
				isRefreshing = false;
			}
		}
	};

	$: normalizedChatId = chatId && chatId.trim() ? chatId.trim() : '_new';
	$: if (mounted && normalizedChatId !== currentChatId) {
		currentChatId = normalizedChatId;
		void refreshStats(normalizedChatId, false);
	}

	const handleClickOutside = (e: MouseEvent) => {
		if (showPopover && containerRef && !containerRef.contains(e.target as Node)) {
			showPopover = false;
		}
	};

	const updatePopoverPosition = () => {
		if (showPopover && containerRef) {
			popoverStyle = computeClampedPopoverStyle(containerRef, 368);
		}
	};

	const handleClick = () => {
		if (clickTimer) {
			clearTimeout(clickTimer);
			clickTimer = null;
		}
		clickTimer = setTimeout(() => {
			clickTimer = null;
			showPopover = !showPopover;
			if (showPopover) {
				popoverStyle = computeClampedPopoverStyle(containerRef, 368);
			}
		}, 200);
	};

	const handleDoubleClick = () => {
		if (clickTimer) {
			clearTimeout(clickTimer);
			clickTimer = null;
		}
		void refreshStats(normalizedChatId, true);
	};

	onMount(() => {
		mounted = true;
		window.addEventListener('click', handleClickOutside);
		window.addEventListener('resize', updatePopoverPosition);
		currentChatId = normalizedChatId;
		void refreshStats(normalizedChatId, false);
	});

	onDestroy(() => {
		if (clickTimer) {
			clearTimeout(clickTimer);
			clickTimer = null;
		}
		if (typeof window !== 'undefined') {
			window.removeEventListener('click', handleClickOutside);
			window.removeEventListener('resize', updatePopoverPosition);
		}
	});

	$: stats = $echoContextGauge;
	$: maxTokens = stats?.max_tokens || DEFAULT_MAX_TOKENS;
	$: cachedTokens = stats?.cached_tokens ?? stats?.cachedContentTokenCount ?? 0;
	$: rawPromptTokens = stats?.prompt_tokens ?? stats?.promptTokenCount ?? 0;
	$: activePromptTokens = Math.max(0, rawPromptTokens - cachedTokens);
	$: genTokens = stats?.generation_tokens ?? stats?.candidatesTokenCount ?? 0;
	$: totalTokens =
		stats?.total_tokens ??
		stats?.totalTokenCount ??
		(cachedTokens + activePromptTokens + genTokens);

	$: cachePct = Math.min(100, Math.max(0, (cachedTokens / maxTokens) * 100));
	$: promptPct = Math.min(100 - cachePct, Math.max(0, (activePromptTokens / maxTokens) * 100));
	$: genPct = Math.min(100 - cachePct - promptPct, Math.max(0, (genTokens / maxTokens) * 100));

	$: pct =
		stats?.percentage !== undefined
			? Math.min(100, Math.max(0, stats.percentage))
			: Math.min(100, Math.max(0, (totalTokens / maxTokens) * 100));
	$: pctFormatted = pct.toFixed(1);

	$: compactions = stats?.compaction_count ?? 0;
	$: compactedTurns = stats?.compacted_turns ?? 0;
	$: isPending = stats?.is_pending ?? false;

	$: deltaObj = stats?.delta ?? null;
	$: deltaTotal = deltaObj?.delta_total ?? stats?.delta_tokens ?? stats?.delta_capacity ?? 0;
	$: capacityLostPct = typeof deltaObj?.capacity_lost_pct === 'number' ? deltaObj.capacity_lost_pct : 0;
	$: growthPct = typeof deltaObj?.growth_pct === 'number' ? deltaObj.growth_pct : 0;

	// Identité Google & Quotas
	$: userEmail = stats?.user_email || '';
	$: userTier = stats?.user_tier || '';
	$: authSources = Array.isArray(stats?.auth_sources) ? stats.auth_sources : [];
	$: projectId = stats?.project_id || '';
	$: tenantId = stats?.tenant_id || 'aicode-consumers';
	$: creditsVal = stats?.credits_val ?? 'N/A';
	$: quotaType = stats?.quota_type || 'UNKNOWN';

	$: quotaFrac =
		typeof stats?.quota_fraction === 'number'
			? Math.min(1, Math.max(0, stats.quota_fraction))
			: 1.0;
	$: quotaRingColor =
		quotaFrac < 0.2 ? '#ef4444' : quotaFrac < 0.5 ? '#f59e0b' : '#10b981';
	$: ringDashOffset = RING_CIRCUMFERENCE * (1 - quotaFrac);

	$: quota5hPct =
		typeof stats?.quota_5h_fraction === 'number'
			? Math.round(stats.quota_5h_fraction * 100)
			: stats?.quotas?.five_hour_pct;
	$: quota5hReset = stats?.quota_5h_reset || 'N/A';
	$: quota5hDisabled = Boolean(stats?.quota_5h_disabled);
	$: deltaQuota5h = typeof deltaObj?.delta_quota_5h === 'number' ? deltaObj.delta_quota_5h : null;

	$: quotaWeeklyPct =
		typeof stats?.quota_weekly_fraction === 'number'
			? Math.round(stats.quota_weekly_fraction * 100)
			: stats?.quotas?.weekly_pct;
	$: quotaWeeklyReset = stats?.quota_weekly_reset || 'N/A';
	$: deltaQuotaWeekly =
		typeof deltaObj?.delta_quota_weekly === 'number' ? deltaObj.delta_quota_weekly : null;

	$: rpdRem = stats?.quota_rpd_rem ?? stats?.quotas?.rpd_usage;
	$: rpdLim = stats?.quota_rpd_lim ?? stats?.quotas?.rpd_limit;
	$: rpmRem = stats?.quota_rpm_rem;
	$: rpmLim = stats?.quota_rpm_lim;

	$: shortIdentityLabel = userEmail
		? userEmail.split('@')[0]
		: userTier || '';

	$: badgeBorder =
		pct > 80
			? 'border-red-500/40 hover:border-red-500/70'
			: pct > 50
				? 'border-amber-500/40 hover:border-amber-500/70'
				: 'border-gray-200/80 dark:border-gray-700/80 hover:border-emerald-500/50';

	const formatNumber = (num: number): string => {
		if (num >= 1_000_000) return (num / 1_000_000).toFixed(2) + 'M';
		if (num >= 1_000) return (num / 1_000).toFixed(1) + 'k';
		return num.toLocaleString();
	};

	const formatQuotaColor = (pctVal: number): string => {
		if (pctVal < 20) return 'text-red-500';
		if (pctVal < 50) return 'text-amber-500';
		return 'text-emerald-500';
	};
</script>

<div class="echo-gauge-root relative flex flex-1 items-center min-w-[96px] max-w-full" bind:this={containerRef}>
	<button
		type="button"
		class="w-full h-[1.875rem] flex items-center gap-1.5 px-2.5 text-xs font-mono font-medium rounded-xl border bg-gray-100/80 dark:bg-gray-800/80 hover:bg-gray-200/70 dark:hover:bg-gray-700/80 transition shadow-xs select-none {badgeBorder}"
		on:click={handleClick}
		on:dblclick|preventDefault|stopPropagation={handleDoubleClick}
		title="Contexte, session & identité Google (clic : détails · double-clic : rafraîchir)"
		aria-label="Jauge contextuelle et identité Google"
	>
		<!-- 1. Identité Google & Quota Ring (gauche) -->
		<div class="shrink-0 flex items-center gap-1.5 min-w-0">
			<svg
				width="18"
				height="18"
				viewBox="0 0 18 18"
				class="shrink-0 {isRefreshing ? 'animate-spin' : isPending ? 'animate-pulse' : ''}"
			>
				<circle
					cx="9"
					cy="9"
					r={RING_RADIUS}
					fill="none"
					stroke="currentColor"
					class="text-gray-300 dark:text-gray-600/60"
					stroke-width="2"
				/>
				<circle
					cx="9"
					cy="9"
					r={RING_RADIUS}
					fill="none"
					stroke={quotaRingColor}
					stroke-width="2"
					stroke-dasharray={RING_CIRCUMFERENCE}
					stroke-dashoffset={ringDashOffset}
					transform="rotate(-90 9 9)"
					stroke-linecap="round"
				/>
				<path
					d="M9 5.5a2 2 0 0 0-2 2V9h4V7.5a2 2 0 0 0-2-2Zm2.75 3.5h-5.5a.75.75 0 0 0-.75.75v3a.75.75 0 0 0 .75.75h5.5a.75.75 0 0 0 .75-.75v-3a.75.75 0 0 0-.75-.75Z"
					fill="currentColor"
					class="text-gray-700 dark:text-gray-200"
					opacity="0.85"
				/>
			</svg>
			{#if shortIdentityLabel}
				<span class="echo-gauge-identity truncate max-w-[105px] text-[11px] text-gray-600 dark:text-gray-300">
					{shortIdentityLabel}
				</span>
			{/if}
		</div>

		<!-- 2. Barre de Contexte 3 segments redimensionnable (centre) -->
		<div class="flex flex-1 min-w-[28px] h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
			{#if cachePct > 0}
				<div
					class="h-full bg-violet-500 transition-all duration-300"
					style="width: {cachePct}%"
				></div>
			{/if}
			{#if promptPct > 0}
				<div
					class="h-full bg-emerald-500 transition-all duration-300"
					style="width: {promptPct}%"
				></div>
			{/if}
			{#if genPct > 0}
				<div
					class="h-full bg-amber-500 transition-all duration-300"
					style="width: {genPct}%"
				></div>
			{/if}
		</div>

		<!-- 3. Session & Différentiel (droite) -->
		<div class="shrink-0 flex items-center gap-1">
			<span class="text-gray-800 dark:text-gray-200 font-semibold">{pctFormatted}%</span>
			<span class="echo-gauge-tokens text-gray-400 dark:text-gray-500">
				({formatNumber(totalTokens)})
			</span>
			{#if capacityLostPct !== 0}
				<span
					class="echo-gauge-delta items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium border {capacityLostPct > 0
						? 'bg-red-500/10 text-red-500 border-red-500/25'
						: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/25'}"
				>
					{capacityLostPct > 0 ? '-' : '+'}{Math.abs(capacityLostPct).toFixed(2)}%
				</span>
			{/if}
		</div>
	</button>

	<!-- Popover détaillé vers le haut -->
	{#if showPopover}
		<div
			style={popoverStyle}
			class="bottom-full mb-2 absolute max-h-[min(75dvh,28rem)] overflow-y-auto p-4 rounded-xl shadow-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-xs z-50 animate-in fade-in zoom-in-95 duration-150"
		>
			<!-- En-tête -->
			<div class="flex items-center justify-between pb-2 mb-3 border-b border-gray-100 dark:border-gray-800">
				<div class="flex flex-col">
					<span class="font-semibold text-gray-900 dark:text-white">
						Contexte, Session &amp; Identité
					</span>
					<span class="text-[10px] text-gray-400 dark:text-gray-500">
						Double-clic sur la barre pour rafraîchir
					</span>
				</div>
				<button
					type="button"
					class="p-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition"
					on:click|stopPropagation={() => refreshStats(normalizedChatId, true)}
					title="Rafraîchir maintenant"
					aria-label="Rafraîchir les métriques"
					disabled={isRefreshing}
				>
					<svg
						class="w-3.5 h-3.5 {isRefreshing ? 'animate-spin text-emerald-500' : ''}"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
						<path d="M21 3v5h-5" />
					</svg>
				</button>
			</div>

			<!-- Section 1 : Contexte & Session -->
			<div class="space-y-2 mb-3">
				<div class="flex justify-between font-mono text-gray-600 dark:text-gray-400">
					<span>Total Tokens :</span>
					<span class="font-bold text-gray-900 dark:text-white">
						{totalTokens.toLocaleString()} / {formatNumber(maxTokens)} ({pctFormatted}%)
					</span>
				</div>
				<div class="flex w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
					{#if cachePct > 0}
						<div class="h-full bg-violet-500" style="width: {cachePct}%"></div>
					{/if}
					{#if promptPct > 0}
						<div class="h-full bg-emerald-500" style="width: {promptPct}%"></div>
					{/if}
					{#if genPct > 0}
						<div class="h-full bg-amber-500" style="width: {genPct}%"></div>
					{/if}
				</div>

				<div class="space-y-1 pt-1 font-mono text-[11px]">
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span class="flex items-center gap-1.5">
							<span class="w-2 h-2 rounded-full bg-violet-500"></span>
							Cache :
						</span>
						<span class="text-violet-500 font-semibold">{cachedTokens.toLocaleString()}</span>
					</div>
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span class="flex items-center gap-1.5">
							<span class="w-2 h-2 rounded-full bg-emerald-500"></span>
							Prompt actif :
						</span>
						<span class="text-emerald-500 font-semibold">{activePromptTokens.toLocaleString()}</span>
					</div>
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span class="flex items-center gap-1.5">
							<span class="w-2 h-2 rounded-full bg-amber-500"></span>
							Génération :
						</span>
						<span class="text-amber-500 font-semibold">{genTokens.toLocaleString()}</span>
					</div>
					{#if compactions > 0}
						<div class="flex justify-between text-cyan-500">
							<span>Compactions :</span>
							<span class="font-semibold">#{compactions} ({compactedTurns} tours)</span>
						</div>
					{/if}
					{#if deltaTotal !== 0 || capacityLostPct !== 0 || growthPct !== 0}
						<div class="flex justify-between text-gray-500 pt-1 border-t border-gray-100 dark:border-gray-800/70">
							<span>Différentiel tour :</span>
							<span class="font-semibold text-gray-800 dark:text-gray-200">
								{deltaTotal > 0 ? '+' : ''}{deltaTotal.toLocaleString()} tok
								{#if capacityLostPct !== 0}
									({capacityLostPct > 0 ? '-' : '+'}{Math.abs(capacityLostPct).toFixed(2)}%)
								{/if}
								{#if growthPct !== 0}
									· {growthPct > 0 ? '+' : ''}{growthPct}%
								{/if}
							</span>
						</div>
					{/if}
				</div>
			</div>

			<!-- Section 2 : Identité Google & Authentification -->
			<div class="py-2.5 border-t border-gray-100 dark:border-gray-800 space-y-1 font-mono text-[11px]">
				<div class="font-sans font-semibold text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
					Identité Google &amp; Authentification
				</div>
				<div class="flex justify-between gap-2 text-gray-600 dark:text-gray-400">
					<span>Source :</span>
					<span class="font-semibold text-gray-900 dark:text-gray-100 truncate">
						{authSources.length > 0 ? authSources.join(', ') : 'N/A'}
					</span>
				</div>
				<div class="flex justify-between gap-2 text-gray-600 dark:text-gray-400">
					<span>Compte :</span>
					<span class="font-semibold text-gray-900 dark:text-gray-100 truncate" title={userEmail}>
						{userEmail || 'N/A'}
					</span>
				</div>
				{#if userTier}
					<div class="flex justify-between gap-2 text-gray-600 dark:text-gray-400">
						<span>Tier :</span>
						<span class="font-semibold text-sky-500">{userTier}</span>
					</div>
				{/if}
				{#if projectId && projectId !== tenantId}
					<div class="flex justify-between gap-2 text-gray-600 dark:text-gray-400">
						<span>Projet Perso :</span>
						<span class="truncate text-gray-800 dark:text-gray-200">{projectId}</span>
					</div>
				{/if}
				<div class="flex justify-between gap-2 text-gray-600 dark:text-gray-400">
					<span>Tenant :</span>
					<span class="text-emerald-500 font-semibold truncate">{tenantId}</span>
				</div>
			</div>

			<!-- Section 3 : Quotas Cloud -->
			<div class="pt-2.5 border-t border-gray-100 dark:border-gray-800 space-y-1 font-mono text-[11px]">
				<div class="font-sans font-semibold text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
					Quotas Cloud
				</div>
				<div class="flex justify-between text-gray-600 dark:text-gray-400">
					<span>Crédits IA :</span>
					<span class="font-semibold text-emerald-500">{creditsVal}</span>
				</div>
				{#if quota5hDisabled}
					<div class="flex justify-between text-gray-500">
						<span>Fenêtre 5h :</span>
						<span>Désactivée</span>
					</div>
				{:else if quota5hPct !== undefined}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>Fenêtre 5h :</span>
						<span>
							<b class={formatQuotaColor(quota5hPct)}>{quota5hPct}%</b>
							{#if deltaQuota5h !== null}
								<span class="ml-1 text-[10px] {deltaQuota5h < 0 ? 'text-red-500' : deltaQuota5h > 0 ? 'text-emerald-500' : 'text-gray-400'}">
									({deltaQuota5h > 0 ? '+' : ''}{deltaQuota5h}%)
								</span>
							{/if}
							{#if quota5hReset && quota5hReset !== 'N/A'}
								<span class="text-[10px] text-gray-400 ml-1">· {quota5hReset}</span>
							{/if}
						</span>
					</div>
				{/if}
				{#if quotaWeeklyPct !== undefined}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>Hebdomadaire :</span>
						<span>
							<b class={formatQuotaColor(quotaWeeklyPct)}>{quotaWeeklyPct}%</b>
							{#if deltaQuotaWeekly !== null}
								<span class="ml-1 text-[10px] {deltaQuotaWeekly < 0 ? 'text-red-500' : deltaQuotaWeekly > 0 ? 'text-emerald-500' : 'text-gray-400'}">
									({deltaQuotaWeekly > 0 ? '+' : ''}{deltaQuotaWeekly}%)
								</span>
							{/if}
							{#if quotaWeeklyReset && quotaWeeklyReset !== 'N/A'}
								<span class="text-[10px] text-gray-400 ml-1">· {quotaWeeklyReset}</span>
							{/if}
						</span>
					</div>
				{/if}
				{#if rpdRem !== undefined && rpdRem !== 'N/A'}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>RPD :</span>
						<span class="font-semibold">{rpdRem} / {rpdLim ?? '∞'}</span>
					</div>
				{/if}
				{#if rpmRem !== undefined && rpmRem !== 'N/A'}
					<div class="flex justify-between text-gray-600 dark:text-gray-400">
						<span>RPM :</span>
						<span class="font-semibold">{rpmRem} / {rpmLim ?? '∞'}</span>
					</div>
				{/if}
				<div class="flex justify-between text-gray-500">
					<span>Type :</span>
					<span>{quotaType}</span>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.echo-gauge-root {
		container-type: inline-size;
	}
	.echo-gauge-identity,
	.echo-gauge-tokens,
	.echo-gauge-delta {
		display: none;
	}
	@container (min-width: 175px) {
		.echo-gauge-tokens {
			display: inline;
		}
	}
	@container (min-width: 220px) {
		.echo-gauge-delta {
			display: inline-flex;
		}
	}
	@container (min-width: 255px) {
		.echo-gauge-identity {
			display: inline;
		}
	}
</style>
