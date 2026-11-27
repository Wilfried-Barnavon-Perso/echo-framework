<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	export let sessionId = '';
	export let terminalName = 'Sandbox';
	export let terminalMode: 'local' | 'ssh' = 'local';
	export let terminalTarget = '';
	export let agentLocked = false;
	export let onToggleLock: ((nextLocked: boolean) => Promise<void> | void) | null = null;

	let toggling = false;
	let copied = false;
	let copiedTimer: ReturnType<typeof setTimeout> | null = null;

	$: shortSessionId = sessionId ? sessionId.replace(/^term_/, '').slice(0, 8) : '';

	const copySessionId = async () => {
		if (!sessionId) return;
		try {
			await navigator.clipboard.writeText(sessionId);
			copied = true;
			toast.success(`ID copié : ${sessionId}`);
			if (copiedTimer !== null) {
				clearTimeout(copiedTimer);
			}
			copiedTimer = setTimeout(() => {
				copied = false;
				copiedTimer = null;
			}, 1500);
		} catch {
			toast.error("Impossible de copier l'identifiant du terminal.");
		}
	};

	const handleToggleLock = async () => {
		if (toggling || !sessionId || !onToggleLock) return;
		toggling = true;
		try {
			await onToggleLock(!agentLocked);
		} finally {
			toggling = false;
		}
	};

	onDestroy(() => {
		if (copiedTimer !== null) {
			clearTimeout(copiedTimer);
			copiedTimer = null;
		}
	});
</script>

<div
	class="h-7 shrink-0 px-2.5 border-b border-gray-200/70 dark:border-gray-800/80 bg-gray-50/70 dark:bg-gray-900/50 flex items-center justify-between gap-2 text-xs select-none"
>
	<div class="min-w-0 flex items-center gap-1.5">
		{#if terminalMode === 'ssh'}
			<Tooltip
				content={terminalTarget ? `Session SSH : ${terminalTarget}` : 'Session SSH distante'}
				className="inline-flex shrink-0"
			>
				<span
					class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/25"
				>
					SSH
				</span>
			</Tooltip>
			{#if terminalTarget}
				<span
					class="text-[11px] font-mono text-gray-500 dark:text-gray-400 truncate max-w-44"
					title={terminalTarget}
				>
					{terminalTarget}
				</span>
			{/if}
		{:else}
			<Tooltip content="Sandbox locale non identifiée" className="inline-flex shrink-0">
				<span
					class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20"
				>
					Sandbox
				</span>
			</Tooltip>
		{/if}

		<span class="font-medium text-gray-700 dark:text-gray-200 truncate">
			{terminalName || (terminalMode === 'ssh' ? 'SSH' : 'Sandbox')}
		</span>

		{#if sessionId}
			<Tooltip
				content={copied ? 'Identifiant copié !' : `Copier l'ID complet (${sessionId})`}
				className="inline-flex shrink-0"
			>
				<button
					type="button"
					class="px-1.5 py-0.5 rounded font-mono text-[10px] bg-gray-200/70 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-300/70 dark:hover:bg-gray-700 transition"
					on:click={copySessionId}
				>
					{copied ? '✓ Copié' : `#${shortSessionId}`}
				</button>
			</Tooltip>
		{/if}
	</div>

	<div class="shrink-0 flex items-center">
		<Tooltip
			content={agentLocked
				? "L'Agent peut lire ce terminal mais ne peut pas y interagir. Cliquez pour déverrouiller."
				: "L'Agent peut lire et interagir avec ce terminal. Cliquez pour verrouiller en lecture seule."}
			placement="left"
			className="inline-flex shrink-0"
		>
			<button
				type="button"
				disabled={toggling || !sessionId || !onToggleLock}
				class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium border transition disabled:opacity-50 disabled:cursor-not-allowed {agentLocked
					? 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30 hover:bg-amber-500/25'
					: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25 hover:bg-emerald-500/20'}"
				on:click={handleToggleLock}
			>
				{#if agentLocked}
					<span>🔒 Lecture seule</span>
				{:else}
					<span>🔓 Agent autorisé</span>
				{/if}
			</button>
		</Tooltip>
	</div>
</div>
