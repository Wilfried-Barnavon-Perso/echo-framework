<script lang="ts">
	import DOMPurify from 'dompurify';

	import { onMount, getContext, createEventDispatcher, onDestroy, tick } from 'svelte';
	import * as FocusTrap from 'focus-trap';
	import { settings } from '$lib/stores';
	import { matchKeybinding, Shortcut } from '$lib/shortcuts';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');
	const dispatch = createEventDispatcher();

	import { fade } from 'svelte/transition';
	import { flyAndScale } from '$lib/utils/transitions';
	import { marked } from 'marked';
	import SensitiveInput from './SensitiveInput.svelte';
	import NativeSelect from './NativeSelect.svelte';

	export let title = '';
	export let message = '';

	export let cancelLabel: string | undefined = undefined;
	export let confirmLabel: string | undefined = undefined;
	export let confirmFirst = false;
	export let primaryAction: 'confirm' | 'cancel' = 'confirm';

	export let onConfirm = () => {};

	export let input = false;
	export let inputPlaceholder = '';
	export let inputValue = '';
	export let inputType = '';
	export let inputOptions: ({ label?: string; value: string } | string)[] = [];

	let _inputValue = inputValue;

	export let show = false;
	export let timeoutMs: number | null = null;

	let wasOpen = false;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
	let countdownHandle: ReturnType<typeof setInterval> | null = null;
	let remainingSeconds: number | null = null;

	const clearAutoCancel = () => {
		if (timeoutHandle) {
			clearTimeout(timeoutHandle);
			timeoutHandle = null;
		}
		if (countdownHandle) {
			clearInterval(countdownHandle);
			countdownHandle = null;
		}
		remainingSeconds = null;
	};

	const formatCountdown = (sec: number): string => {
		if (sec >= 60) {
			const m = Math.floor(sec / 60);
			const s = sec % 60;
			return `${m}:${String(s).padStart(2, '0')}`;
		}
		return `${sec}s`;
	};

	$: if (show && !wasOpen) {
		wasOpen = true;
		init();
		clearAutoCancel();
		if (typeof timeoutMs === 'number' && timeoutMs > 0) {
			remainingSeconds = Math.ceil(timeoutMs / 1000);
			countdownHandle = setInterval(() => {
				remainingSeconds =
					remainingSeconds !== null && remainingSeconds > 0 ? remainingSeconds - 1 : 0;
			}, 1000);
			timeoutHandle = setTimeout(() => {
				cancelHandler();
			}, timeoutMs);
		}
	}

	$: if (!show && wasOpen) {
		clearAutoCancel();
		wasOpen = false;
	}

	let modalElement = null;
	let mounted = false;

	let focusTrap: FocusTrap.FocusTrap | null = null;
	let returnFocusTarget: HTMLElement | null = null;

	const handleFocusOutside = (event: FocusEvent) => {
		if (
			event.target instanceof HTMLElement &&
			!modalElement?.contains(event.target) &&
			!event.target.closest('[role="menu"], [role="listbox"]')
		) {
			returnFocusTarget = event.target;
		}
	};

	const getReturnFocus = (previous: HTMLElement | SVGElement) =>
		previous.isConnected ? previous : (returnFocusTarget ?? previous);

	let previousOverflow: string | null = null;
	const restoreOverflow = () => {
		if (previousOverflow !== null) {
			document.body.style.overflow = previousOverflow;
			previousOverflow = null;
		}
	};

	const init = () => {
		_inputValue = inputValue;
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (
			event.key === 'Escape' ||
			($settings?.keyboardShortcuts !== false && matchKeybinding(event) === Shortcut.CLOSE_MODAL)
		) {
			cancelHandler();
		}

		if (event.key === 'Enter') {
			// let the focused control act on Enter itself
			const target = event.target;
			if (target instanceof Element && target.closest('a, button, textarea')) {
				return;
			}

			event.preventDefault();
			event.stopPropagation();
			confirmHandler();
		}
	};

	const confirmHandler = async () => {
		clearAutoCancel();
		show = false;
		await tick();
		await onConfirm();
		dispatch('confirm', _inputValue);
	};

	const cancelHandler = () => {
		clearAutoCancel();
		show = false;
		dispatch('cancel');
	};

	onMount(() => {
		mounted = true;
	});

	$: if (mounted) {
		if (show && modalElement) {
			document.body.appendChild(modalElement);
			returnFocusTarget = null;
			focusTrap = FocusTrap.createFocusTrap(modalElement, { setReturnFocus: getReturnFocus });
			focusTrap.activate();

			window.addEventListener('keydown', handleKeyDown);
			window.addEventListener('focusin', handleFocusOutside, true);
			previousOverflow = document.body.style.overflow;
			document.body.style.overflow = 'hidden';
		} else if (modalElement) {
			focusTrap.deactivate();

			window.removeEventListener('keydown', handleKeyDown);
			window.removeEventListener('focusin', handleFocusOutside, true);
			document.body.removeChild(modalElement);

			restoreOverflow();
		}
	}

	onDestroy(() => {
		clearAutoCancel();
		show = false;
		restoreOverflow();
		window.removeEventListener('keydown', handleKeyDown);
		window.removeEventListener('focusin', handleFocusOutside, true);
		if (focusTrap) {
			focusTrap.deactivate();
		}
		if (modalElement) {
			document.body.removeChild(modalElement);
		}
	});
</script>

{#if show}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		bind:this={modalElement}
		class="modal fixed top-0 right-0 left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] flex justify-center z-99999999 overflow-hidden overscroll-contain"
		in:fade={{ duration: 10 }}
		on:mousedown={() => {
			cancelHandler();
		}}
	>
		<div
			role="dialog"
			aria-modal="true"
			aria-label={title !== '' ? title : $i18n.t('Confirm your action')}
			tabindex="-1"
			class="m-auto max-w-full w-[32rem] mx-2 bg-white dark:bg-gray-950 rounded-3xl max-h-[100dvh] shadow-3xl border border-white dark:border-gray-900"
			in:flyAndScale
			on:mousedown={(e) => {
				e.stopPropagation();
			}}
		>
			<div class="px-5 py-5 flex flex-col">
				<div class="text-sm font-medium dark:text-gray-200 mb-2.5">
					{#if title !== ''}
						{title}
					{:else}
						{$i18n.t('Confirm your action')}
					{/if}
				</div>

				<slot>
					<div class=" text-sm text-gray-500 flex-1">
						{#if message !== ''}
							{@const html = DOMPurify.sanitize(marked.parse(message))}
							<div
								class="max-h-[55vh] overflow-y-auto pr-1 [&_pre]:mt-2 [&_pre]:max-h-[38vh] [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:bg-gray-100 dark:[&_pre]:bg-gray-900 [&_pre]:p-2.5 [&_pre]:font-mono [&_pre]:text-xs [&_pre]:text-gray-800 dark:[&_pre]:text-gray-200 [&_code]:rounded [&_code]:bg-gray-100 dark:[&_code]:bg-gray-900 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:my-0.5"
							>
								{@html html}
							</div>
						{:else}
							{$i18n.t('This action cannot be undone. Do you wish to continue?')}
						{/if}

						{#if input}
							{#if inputType === 'password'}
								<div
									class="w-full mt-2 rounded-lg px-4 py-2 text-sm dark:text-gray-300 dark:bg-gray-900"
								>
									<SensitiveInput
										id="event-confirm-input"
										placeholder={inputPlaceholder
											? inputPlaceholder
											: $i18n.t('Enter your message')}
										bind:value={_inputValue}
										required={true}
									/>
								</div>
							{:else if inputType === 'select' && inputOptions.length}
								<NativeSelect
									className="w-full mt-2 rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-900 outline-hidden"
									bind:value={_inputValue}
									options={inputOptions}
									placeholder={inputPlaceholder ? inputPlaceholder : $i18n.t('Select an option')}
									required
								/>
							{:else}
								<textarea
									bind:value={_inputValue}
									aria-label={inputPlaceholder ? inputPlaceholder : $i18n.t('Enter your message')}
									placeholder={inputPlaceholder ? inputPlaceholder : $i18n.t('Enter your message')}
									class="w-full mt-2 rounded-lg px-4 py-2 text-sm dark:text-gray-300 dark:bg-gray-900 outline-hidden resize-none"
									rows="3"
									required
								/>
							{/if}
						{/if}
					</div>
				</slot>

				<div class="mt-5 flex justify-between gap-1.5">
					{#each confirmFirst ? ['confirm', 'cancel'] : ['cancel', 'confirm'] as action (action)}
						<button
							class="flex h-7 w-full items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal transition disabled:opacity-60 {action ===
							primaryAction
								? 'bg-gray-900 text-white hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white'
								: 'bg-gray-100 hover:bg-gray-100/70 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-850/60 dark:text-white'}"
							on:click={action === 'confirm' ? confirmHandler : cancelHandler}
							type="button"
						>
							{action === 'confirm'
								? (confirmLabel ?? $i18n.t('Confirm'))
								: remainingSeconds !== null
									? `${cancelLabel ?? $i18n.t('Cancel')} (${formatCountdown(remainingSeconds)})`
									: (cancelLabel ?? $i18n.t('Cancel'))}
						</button>
					{/each}
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-content {
		animation: scaleUp 0.1s ease-out forwards;
	}

	@keyframes scaleUp {
		from {
			transform: scale(0.985);
			opacity: 0;
		}
		to {
			transform: scale(1);
			opacity: 1;
		}
	}
</style>
