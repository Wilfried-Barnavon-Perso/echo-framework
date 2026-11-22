<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { showTabs } from '$lib/stores';
	import {
		acquireCognitionStream,
		echoCognitionActiveCount,
		echoCognitionRunningCount,
		requestEchoTabOpen
	} from '$lib/stores/echo';
	import { isSavedChatId } from '$lib/utils/chatId';

	export let chatId: string = '';

	let mounted = false;
	let boundChatId = '';
	let releaseStream: (() => void) | null = null;

	const syncStream = (cid: string) => {
		const normalized = cid && isSavedChatId(cid) ? cid : '';
		if (normalized === boundChatId) return;
		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = normalized;
		if (normalized) {
			releaseStream = acquireCognitionStream(normalized);
		} else {
			// Réinitialise proprement lors d'un basculement vers un chat non sauvegardé
			const cleanup = acquireCognitionStream('');
			cleanup();
		}
	};

	onMount(() => {
		mounted = true;
		syncStream(chatId);
	});

	$: if (mounted) {
		syncStream(chatId);
	}

	onDestroy(() => {
		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = '';
	});

	$: totalCount = $echoCognitionActiveCount;
	$: runningCount = $echoCognitionRunningCount;
	$: visible = totalCount > 0 || runningCount > 0;
	$: displayCount = runningCount > 0 ? runningCount : totalCount;
	$: tooltipText =
		runningCount > 0
			? `${runningCount} sous-agent(s) en cours sur ${totalCount} — Cliquer pour ouvrir`
			: `${totalCount} sous-agent(s) en attente — Cliquer pour ouvrir`;

	const openSubagentsPanel = () => {
		if (!chatId) return;
		showTabs.set(true);
		requestEchoTabOpen(chatId, 'echo-cognition');
	};
</script>

{#if visible}
	<Tooltip content={tooltipText}>
		<button
			type="button"
			class="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono font-medium rounded-lg border bg-gray-50 dark:bg-gray-850 hover:bg-gray-100 dark:hover:bg-gray-800 transition shadow-sm {runningCount > 0
				? 'border-emerald-500/40 hover:border-emerald-500/70'
				: 'border-indigo-500/30 hover:border-indigo-500/60'}"
			on:click={openSubagentsPanel}
			aria-label={tooltipText}
		>
			<span class="relative flex items-center justify-center w-2 h-2">
				{#if runningCount > 0}
					<span
						class="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping bg-emerald-500"
					></span>
					<span class="relative inline-flex rounded-full w-2 h-2 bg-emerald-500"></span>
				{:else}
					<span class="relative inline-flex rounded-full w-2 h-2 bg-indigo-500 dark:bg-indigo-400"></span>
				{/if}
			</span>

			<svg
				class="w-3.5 h-3.5 {runningCount > 0
					? 'text-emerald-600 dark:text-emerald-400'
					: 'text-indigo-600 dark:text-indigo-400'}"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
			>
				<circle cx="12" cy="6" r="3" />
				<circle cx="6" cy="18" r="3" />
				<circle cx="18" cy="18" r="3" />
				<path d="M10.5 8.5L7.5 15.5M13.5 8.5l3 7" />
			</svg>

			<span class="font-semibold text-gray-800 dark:text-gray-200">{displayCount}</span>
			<span class="hidden sm:inline font-sans text-gray-600 dark:text-gray-300">Sous-agents</span>
		</button>
	</Tooltip>
{/if}
