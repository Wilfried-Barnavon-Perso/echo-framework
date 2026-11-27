<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { browser } from '$app/environment';
	import { onMount } from 'svelte';
	import Icon from '$lib/components/chat/FileNav/Icon.svelte';
	import HotkeyHint from '$lib/components/common/HotkeyHint.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { EchoSshTargetItem } from '$lib/apis/echo';
	import { echoSshTargets, refreshEchoSshTargets } from '$lib/stores/echo';

	interface EchoTerminalCreateOpts {
		mode?: 'local' | 'ssh';
		accountId?: string;
		name?: string;
		target?: string;
	}

	export let variant: 'pill' | 'menu' = 'menu';
	export let clickable = true;
	export let disabledReason = '';
	export let onCreate: (opts?: EchoTerminalCreateOpts) => void = () => {};

	const formatTarget = (item: EchoSshTargetItem): string =>
		`${item.user}@${item.host}:${item.port}`;

	const createSshPayload = (item: EchoSshTargetItem): EchoTerminalCreateOpts => ({
		mode: 'ssh',
		accountId: item.account_id,
		name: item.account_id,
		target: formatTarget(item)
	});

	onMount(() => {
		if (clickable) {
			void refreshEchoSshTargets();
		}
	});

	$: if (browser && clickable) {
		void refreshEchoSshTargets();
	}
</script>

{#if variant === 'pill'}
	{#if clickable}
		<button
			type="button"
			class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 dark:border-gray-800 dark:bg-gray-900/70 dark:text-gray-200 dark:hover:bg-gray-800"
			on:click={() => onCreate({ mode: 'local' })}
		>
			<Icon name="terminal" size={14} />
			<span>Terminal Sandbox</span>
		</button>
		{#each $echoSshTargets as targetItem (targetItem.account_id)}
			<Tooltip
				content="{formatTarget(targetItem)}{targetItem.description ? ` — ${targetItem.description}` : ''}"
				className="inline-flex"
			>
				<button
					type="button"
					class="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-800 transition hover:bg-amber-500/20 dark:border-amber-500/25 dark:bg-amber-950/30 dark:text-amber-200 dark:hover:bg-amber-900/40"
					on:click={() => onCreate(createSshPayload(targetItem))}
				>
					<Icon name="terminal" size={14} />
					<span>SSH · {targetItem.account_id}</span>
				</button>
			</Tooltip>
		{/each}
	{/if}
{:else if clickable}
	<button
		type="button"
		on:click={() => onCreate({ mode: 'local' })}
	>
		<Icon name="terminal" size={14} />
		<span class="flex-1 text-start">Nouveau terminal (Sandbox)</span>
		<HotkeyHint name="newTerminal" className="ms-auto" />
	</button>
	{#if $echoSshTargets.length > 0}
		{#each $echoSshTargets as targetItem (targetItem.account_id)}
			<button
				type="button"
				title={targetItem.description || formatTarget(targetItem)}
				on:click={() => onCreate(createSshPayload(targetItem))}
			>
				<Icon name="terminal" size={14} />
				<span class="truncate text-start">SSH · {targetItem.account_id}</span>
				<span class="text-[11px] text-gray-400 ms-auto truncate max-w-28">
					{targetItem.user}@{targetItem.host}
				</span>
			</button>
		{/each}
	{/if}
{:else}
	<Tooltip
		content={disabledReason}
		placement="left"
		className="flex w-full [&>button]:flex [&>button]:w-full [&>button]:h-[1.6875rem] [&>button]:items-center [&>button]:gap-2 [&>button]:rounded-xl! [&>button]:px-2! [&>button]:text-[0.8125rem] [&>button_svg]:size-3.5"
	>
		<button
			type="button"
			disabled
			aria-disabled="true"
			class="opacity-45 cursor-not-allowed text-gray-400 dark:text-gray-500"
			on:click|preventDefault|stopPropagation
		>
			<Icon name="terminal" size={14} />
			<span class="flex-1 text-start">Nouveau terminal (Sandbox)</span>
			<HotkeyHint name="newTerminal" className="ms-auto" />
		</button>
	</Tooltip>
{/if}
