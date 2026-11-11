<script lang="ts">
	import { models, pinnedModels, settings, user } from '$lib/stores';
	import { getContext } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let selectedModels = ['echo_orchestrator'];
	export let onModelSelect: (models: string[]) => void = () => {};
	export let disabled = false;
	export let multipleEnabled = true;

	export let showSetDefault = true;
	export let triggerClassName = 'text-lg';
	export let className = undefined;
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';

	let compareModels = false;

	// [ECHO-V6-HOOK] Verrouillage réactif strict sur le modèle souverain echo_orchestrator
	$: if (selectedModels.length !== 1 || selectedModels[0] !== 'echo_orchestrator') {
		selectedModels = ['echo_orchestrator'];
		compareModels = false;
		onModelSelect(selectedModels);
	}

	export const open = () => {};
</script>

<div class="flex min-w-0 max-w-full flex-col items-start select-none">
	<Tooltip content="Cortex Cognitif Dynamique (Zéphyr · Apollon · Prométhée)" placement="top">
		<div
			class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl bg-gray-100/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 text-gray-900 dark:text-gray-100 cursor-default shadow-xs transition"
		>
			<span class="size-2 rounded-full bg-emerald-500 animate-pulse"></span>
			<span class="text-sm font-semibold tracking-wide">Écho</span>
			<span class="text-[11px] font-mono text-gray-400 dark:text-gray-400 font-medium">v6</span>
		</div>
	</Tooltip>
</div>
