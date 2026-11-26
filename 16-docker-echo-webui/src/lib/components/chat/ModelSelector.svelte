<script lang="ts">
	import { models, pinnedModels, settings, user } from '$lib/stores';
	import { getContext, onMount } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { echoActiveModel, getEchoModelBadge } from '$lib/stores/echo';
	import { getEchoToken, postEchoWmRpc } from '$lib/apis/echo';
	import { getUserValvesById } from '$lib/apis/functions';

	const i18n = getContext('i18n');

	export let selectedModels = ['echo_orchestrator'];
	export let onModelSelect: (models: string[]) => void = () => {};
	export let chatId: string = '';
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

	let mounted = false;
	let currentChatId = '__init__';
	let fetchSeq = 0;
	let cognitiveModeLabel: 'Auto' | 'Manual' = 'Auto';

	export const refreshCognitiveMode = async () => {
		const token = getEchoToken();
		if (!token) return;
		try {
			const res = await getUserValvesById(token, 'echo_orchestrator');
			const raw =
				res?.COGNITIVE_MODE ??
				(typeof res?.MODEL_POLICY === 'string' && res.MODEL_POLICY.startsWith('MANUAL')
					? 'MANUAL'
					: 'AUTO');
			cognitiveModeLabel = raw === 'MANUAL' ? 'Manual' : 'Auto';
		} catch {
			// Défaut Auto conservé
		}
	};

	const refreshActiveModel = async (cid: string) => {
		const seq = ++fetchSeq;
		if (!cid || cid === '_new' || cid.startsWith('local:')) {
			echoActiveModel.set(null);
			return;
		}
		const token = getEchoToken();
		if (!token) return;
		try {
			const res = await postEchoWmRpc(token, { action: 'get_active_model', chat_id: cid });
			if (seq !== fetchSeq || cid !== currentChatId) return;
			const mk =
				typeof res?.model_key === 'string' && res.model_key.trim() ? res.model_key.trim() : null;
			echoActiveModel.set(mk);
		} catch {
			// Silencieux si pas encore de session
		}
	};

	$: normalizedChatId = chatId && chatId.trim() ? chatId.trim() : '_new';
	$: if (mounted && normalizedChatId !== currentChatId) {
		currentChatId = normalizedChatId;
		void refreshActiveModel(normalizedChatId);
	}

	onMount(() => {
		mounted = true;
		currentChatId = normalizedChatId;
		void refreshActiveModel(normalizedChatId);
		void refreshCognitiveMode();
	});

	$: activeBadge = getEchoModelBadge($echoActiveModel);
	$: tooltipText = activeBadge
		? `Cortex ${cognitiveModeLabel} · ${activeBadge.label}`
		: `Cortex ${cognitiveModeLabel}`;
</script>

<div class="flex min-w-0 max-w-full items-center select-none shrink-0">
	<Tooltip content={tooltipText} placement="top">
		<div
			class="inline-flex h-[1.875rem] items-center gap-1.5 px-2.5 rounded-xl bg-gray-100/80 dark:bg-gray-800/80 border border-gray-200/80 dark:border-gray-700/80 text-gray-900 dark:text-gray-100 cursor-default shadow-xs transition"
		>
			<span class="size-2 shrink-0 rounded-full bg-emerald-500 animate-pulse"></span>
			<span class="text-xs font-semibold tracking-wide leading-none">Écho</span>
			{#if activeBadge}
				<span
					class="echo-model-active-name text-[10px] font-medium leading-none text-gray-500 dark:text-gray-400 border-l border-gray-300/70 dark:border-gray-600/70 pl-1.5"
				>
					{activeBadge.label}
				</span>
			{/if}
		</div>
	</Tooltip>
</div>
