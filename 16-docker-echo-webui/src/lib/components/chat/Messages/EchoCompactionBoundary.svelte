<script lang="ts">
	import type { EchoCompactionCheckpoint } from '$lib/apis/echo/chat_actions';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let checkpoint: EchoCompactionCheckpoint | null = null;
	export let hasOlder = false;
	export let canLoadOlder: boolean | undefined = undefined;
	export let loading = false;
	export let onLoadOlder: ((checkpoint?: EchoCompactionCheckpoint | null) => void | Promise<void>) | null =
		null;

	$: showLoadOlder = Boolean(canLoadOlder ?? hasOlder);

	const handleClick = async () => {
		if (loading || !showLoadOlder || !onLoadOlder) return;
		await onLoadOlder(checkpoint);
	};
</script>

<div
	class="mx-auto my-3 flex w-full max-w-[58rem] items-center gap-3 px-3.5 text-xs text-gray-500 dark:text-gray-400 select-none"
	role="separator"
>
	<div class="h-px flex-1 bg-gray-200/80 dark:bg-gray-800/80"></div>

	<div class="flex flex-wrap items-center justify-center gap-2">
		{#if checkpoint}
			<span class="inline-flex items-center gap-1.5 rounded-full border border-gray-200/80 bg-gray-50/70 px-2.5 py-0.5 text-[0.7rem] font-medium text-gray-600 dark:border-gray-800 dark:bg-gray-900/60 dark:text-gray-300">
				<span>Compaction #{checkpoint.compaction_count}</span>
				{#if Number(checkpoint.pivot_turn_idx) > 0}
					<span class="text-gray-400 dark:text-gray-500">·</span>
					<span class="text-gray-500 dark:text-gray-400">
						{checkpoint.pivot_turn_idx} tours consolidés
					</span>
				{/if}
			</span>
		{/if}

		{#if showLoadOlder}
			<button
				type="button"
				disabled={loading}
				on:click={handleClick}
				class="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-0.5 text-[0.7rem] font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-850 dark:text-gray-200 dark:hover:bg-gray-800"
			>
				{#if loading}
					<Spinner className="size-3" />
					<span>Chargement...</span>
				{:else}
					<span>Charger les messages précédents</span>
				{/if}
			</button>
		{/if}
	</div>

	<div class="h-px flex-1 bg-gray-200/80 dark:bg-gray-800/80"></div>
</div>
