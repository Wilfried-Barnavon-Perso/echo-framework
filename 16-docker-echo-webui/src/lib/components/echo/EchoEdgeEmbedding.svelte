<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script context="module" lang="ts">
	// Mémorise le masquage sur la session SPA pour éviter un réaffichage de 5s à chaque changement de chat
	let incompatibleHiddenForSession = false;
</script>

<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		computeClampedPopoverStyle,
		echoEdgeEmbedState,
		echoEdgeEmbedProgress,
		echoEdgeEmbedReason,
		echoEdgeEmbedDtype,
		ECHO_EDGE_TARGET_REPO
	} from '$lib/stores/echo';

	const INCOMPATIBLE_HIDE_DELAY_MS = 5000;

	let showPopover = false;
	let popoverStyle = '';
	let containerRef: HTMLDivElement | null = null;
	let hidden = false;
	let hideTimer: ReturnType<typeof setTimeout> | null = null;

	const clearHideTimer = () => {
		if (hideTimer) {
			clearTimeout(hideTimer);
			hideTimer = null;
		}
	};

	const updatePopoverPosition = () => {
		if (showPopover && containerRef) {
			popoverStyle = computeClampedPopoverStyle(containerRef, 288);
		}
	};

	const togglePopover = () => {
		showPopover = !showPopover;
		if (showPopover) {
			popoverStyle = computeClampedPopoverStyle(containerRef, 288);
		}
	};

	const handleClickOutside = (e: MouseEvent) => {
		if (showPopover && containerRef && !containerRef.contains(e.target as Node)) {
			showPopover = false;
		}
	};

	onMount(() => {
		window.addEventListener('click', handleClickOutside);
		window.addEventListener('resize', updatePopoverPosition);
	});

	onDestroy(() => {
		clearHideTimer();
		if (typeof window !== 'undefined') {
			window.removeEventListener('click', handleClickOutside);
			window.removeEventListener('resize', updatePopoverPosition);
		}
	});

	$: state = $echoEdgeEmbedState;
	$: progress = $echoEdgeEmbedProgress;
	$: reason = $echoEdgeEmbedReason;
	$: dtype = $echoEdgeEmbedDtype;

	$: if (state === 'incompatible') {
		if (incompatibleHiddenForSession) {
			hidden = true;
			clearHideTimer();
		} else if (showPopover) {
			clearHideTimer();
		} else if (!hidden && !hideTimer) {
			hideTimer = setTimeout(() => {
				hideTimer = null;
				incompatibleHiddenForSession = true;
				hidden = true;
				showPopover = false;
			}, INCOMPATIBLE_HIDE_DELAY_MS);
		}
	} else {
		clearHideTimer();
		incompatibleHiddenForSession = false;
		hidden = false;
	}

	$: badgeBorder =
		state === 'active'
			? 'border-sky-500/60 bg-sky-500/10 text-sky-600 dark:text-sky-300 shadow-[0_0_8px_rgba(56,189,248,0.25)]'
			: state === 'ready'
				? 'border-sky-500/30 bg-gray-100/80 dark:bg-gray-800/80 text-gray-700 dark:text-gray-200 hover:border-sky-500/50'
				: state === 'downloading'
					? 'border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-300'
					: state === 'probing'
						? 'border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300'
						: state === 'incompatible'
							? 'border-red-500/30 bg-gray-100/80 dark:bg-gray-800/80 text-gray-500 dark:text-gray-400 hover:border-red-500/50'
							: 'border-gray-200/80 dark:border-gray-700/80 bg-gray-100/80 dark:bg-gray-800/80 text-gray-500 dark:text-gray-400';

	$: statusLabel =
		state === 'ready'
			? `Prêt (VRAM ${dtype})`
			: state === 'active'
				? 'Vectorisation en cours...'
				: state === 'downloading'
					? `Téléchargement ONNX (${progress}%)`
					: state === 'probing'
						? 'Sonde matérielle WebGPU...'
						: state === 'incompatible'
							? `Indisponible${reason ? ` : ${reason}` : ''}`
							: 'En attente';
</script>

{#if !hidden}
<div class="relative inline-flex items-center shrink-0" bind:this={containerRef}>
	<button
		type="button"
		class="inline-flex h-[1.875rem] items-center gap-1.5 px-2.5 rounded-xl border text-xs font-mono transition shadow-xs select-none {badgeBorder}"
		on:click={togglePopover}
		title={statusLabel}
		aria-label="État WebGPU Edge Embedding"
	>
		<!-- Point d'état -->
		<span class="relative flex items-center justify-center w-2 h-2 shrink-0">
			{#if state === 'active'}
				<span class="absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 animate-ping"></span>
				<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400"></span>
			{:else if state === 'ready'}
				<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400"></span>
			{:else if state === 'downloading'}
				<span class="relative inline-flex rounded-full w-2 h-2 bg-sky-400 animate-pulse"></span>
			{:else if state === 'probing'}
				<span class="relative inline-flex rounded-full w-2 h-2 bg-amber-400 animate-pulse"></span>
			{:else if state === 'incompatible'}
				<span class="relative inline-flex rounded-full w-2 h-2 bg-red-400/80"></span>
			{:else}
				<span class="relative inline-flex rounded-full w-2 h-2 bg-gray-400"></span>
			{/if}
		</span>

		<!-- Icône puce GPU SVG compacte -->
		<svg
			class="w-3.5 h-3.5 shrink-0"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="1.75"
			stroke-linecap="round"
			stroke-linejoin="round"
		>
			<rect x="6" y="6" width="12" height="12" rx="2" />
			<rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
			<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" />
		</svg>

		{#if state === 'downloading'}
			<span class="font-semibold">{progress}%</span>
		{:else}
			<span class="echo-webgpu-label font-medium">WebGPU</span>
		{/if}
	</button>

	{#if showPopover}
		<div
			style={popoverStyle}
			class="bottom-full mb-2 absolute max-h-[min(75dvh,24rem)] overflow-y-auto p-3.5 rounded-xl shadow-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-xs z-50 animate-in fade-in zoom-in-95 duration-150"
		>
			<div class="flex items-center justify-between pb-2 mb-2.5 border-b border-gray-100 dark:border-gray-800">
				<span class="font-semibold text-gray-900 dark:text-white flex items-center gap-1.5">
					<span
						class="inline-block w-2 h-2 rounded-full {state === 'active' || state === 'ready' || state === 'downloading'
							? 'bg-sky-400'
							: state === 'probing'
								? 'bg-amber-400'
								: state === 'incompatible'
									? 'bg-red-400'
									: 'bg-gray-400'}"
					></span>
					Indexation locale WebGPU
				</span>
				<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-gray-500">
					{dtype}
				</span>
			</div>

			<div class="space-y-2 font-mono text-[11px]">
				<div class="flex justify-between gap-2">
					<span class="text-gray-500 dark:text-gray-400 shrink-0">État :</span>
					<span class="text-right font-semibold text-gray-900 dark:text-gray-100 break-words">
						{statusLabel}
					</span>
				</div>

				{#if state === 'downloading'}
					<div class="w-full h-1.5 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
						<div
							class="h-full bg-sky-400 transition-all duration-200"
							style="width: {progress}%"
						></div>
					</div>
				{/if}

				<div class="pt-1.5 border-t border-gray-100 dark:border-gray-800">
					<div class="text-[10px] text-gray-400 dark:text-gray-500">Modèle ONNX :</div>
					<div class="text-[10px] text-gray-700 dark:text-gray-300 truncate" title={ECHO_EDGE_TARGET_REPO}>
						{ECHO_EDGE_TARGET_REPO}
					</div>
				</div>
			</div>
		</div>
	{/if}
</div>
{/if}
