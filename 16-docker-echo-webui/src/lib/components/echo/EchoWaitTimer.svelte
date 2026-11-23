<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onDestroy } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { clearEchoWaitTimer, echoWaitTimers } from '$lib/stores/echo';

	export let chatId: string = '';

	let remainingSec = 0;
	let intervalHandle: ReturnType<typeof setInterval> | null = null;
	let activeKey = '';

	const stopInterval = () => {
		if (intervalHandle !== null) {
			clearInterval(intervalHandle);
			intervalHandle = null;
		}
	};

	const formatRemaining = (sec: number): string => {
		if (sec >= 60) {
			const m = Math.floor(sec / 60);
			const s = sec % 60;
			return `${m}:${String(s).padStart(2, '0')}`;
		}
		return `${sec}s`;
	};

	$: timer = chatId ? ($echoWaitTimers[chatId] ?? null) : null;
	$: timerKey =
		timer?.active && timer.duration_sec > 0
			? `${chatId}:${timer.started_at_ms}:${timer.duration_sec}`
			: '';

	$: if (timerKey !== activeKey) {
		stopInterval();
		activeKey = timerKey;
		if (timerKey && timer) {
			const targetChatId = chatId;
			const endMs = timer.started_at_ms + timer.duration_sec * 1000;
			const tick = () => {
				const rem = Math.max(0, Math.ceil((endMs - Date.now()) / 1000));
				remainingSec = rem;
				if (rem <= 0) {
					stopInterval();
					activeKey = '';
					clearEchoWaitTimer(targetChatId);
				}
			};
			tick();
			if (remainingSec > 0) {
				intervalHandle = setInterval(tick, 250);
			}
		} else {
			remainingSec = 0;
		}
	}

	onDestroy(stopInterval);

	$: visible = Boolean(timer?.active && remainingSec > 0);
	$: formattedTime = formatRemaining(remainingSec);
	$: tooltipText = `Attente programmée : ${formattedTime} restante(s)`;
</script>

{#if visible}
	<Tooltip content={tooltipText}>
		<div
			class="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono font-medium rounded-lg border bg-gray-50 dark:bg-gray-850 border-amber-500/40 shadow-sm"
			role="timer"
			aria-label={tooltipText}
		>
			<span class="relative flex items-center justify-center w-2 h-2">
				<span
					class="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping bg-amber-500"
				></span>
				<span class="relative inline-flex rounded-full w-2 h-2 bg-amber-500"></span>
			</span>

			<svg
				class="w-3.5 h-3.5 text-amber-600 dark:text-amber-400"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				aria-hidden="true"
			>
				<circle cx="12" cy="12" r="9" />
				<path d="M12 7v5l3 2" stroke-linecap="round" stroke-linejoin="round" />
			</svg>

			<span class="tabular-nums font-semibold text-gray-800 dark:text-gray-200">
				{formattedTime}
			</span>

			<button
				type="button"
				class="ml-0.5 flex h-3.5 w-3.5 items-center justify-center rounded text-gray-400 transition hover:bg-gray-200/70 hover:text-gray-700 dark:hover:bg-gray-700/70 dark:hover:text-gray-200"
				aria-label="Masquer le minuteur"
				on:click={() => clearEchoWaitTimer(chatId)}
			>
				×
			</button>
		</div>
	</Tooltip>
{/if}
