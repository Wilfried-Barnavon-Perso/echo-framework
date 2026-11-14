<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { config, showCallOverlay } from '$lib/stores';
	import type { RealtimeCall } from '$lib/utils/realtime';
	import CallOverlay from './CallOverlay.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext<any>('i18n');
	export let bridge: RealtimeCall | undefined = undefined;
	export let callMode = 'current';
	export let files: any[];
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let modelId: string;
	export let chatId: string;
	export let eventTarget: EventTarget;
	let started = false;
	const dispatch = createEventDispatcher();

	const close = () => {
		showCallOverlay.set(false);
		dispatch('close');
	};

	onMount(() => {
		void bridge;
		callMode = 'current';
		if ($config?.audio?.stt?.engine === 'web') {
			toast.error($i18n.t('Call feature is not supported when using Web STT engine'));
			close();
			return;
		}
		started = true;
	});
</script>

{#if started}
	<CallOverlay
		bind:files
		{submitPrompt}
		{stopResponse}
		{modelId}
		{chatId}
		{eventTarget}
		on:close
	/>
{/if}
