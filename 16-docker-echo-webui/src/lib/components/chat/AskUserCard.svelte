<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { settings } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher();

	type AskUserOption = {
		label: string;
		description?: string;
		recommended?: boolean;
	};

	type AskUserQuestion = {
		id: string;
		header: string;
		question: string;
		options: AskUserOption[];
		allow_other?: boolean;
		show_recommended?: boolean;
		multi_select?: boolean;
	};

	type DraftAnswer =
		| {
				type: 'option';
				option_index: number;
				label: string;
				description: string;
		  }
		| {
				type: 'other';
				text: string;
		  }
		| {
				type: 'multi';
				option_indices: number[];
				labels: string[];
				other_text?: string;
		  };

	export let show = false;
	export let questions: AskUserQuestion[] = [];
	export let allowOther = true;
	export let timeoutMs: number | null = null;

	let answers: Record<string, DraftAnswer> = {};
	let questionIndex = 0;
	let wasOpen = false;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
	let remainingSeconds: number | null = null;
	let countdownHandle: ReturnType<typeof setInterval> | null = null;

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

	const cancel = () => {
		clearAutoCancel();
		show = false;
		dispatch('cancel');
	};

	$: if (show && !wasOpen) {
		answers = {};
		questionIndex = 0;
		wasOpen = true;
		clearAutoCancel();
		if (typeof timeoutMs === 'number' && timeoutMs > 0) {
			remainingSeconds = Math.ceil(timeoutMs / 1000);
			countdownHandle = setInterval(() => {
				remainingSeconds =
					remainingSeconds !== null && remainingSeconds > 0 ? remainingSeconds - 1 : 0;
			}, 1000);
			timeoutHandle = setTimeout(() => {
				cancel();
			}, timeoutMs);
		}
	}

	$: if (!show && wasOpen) {
		clearAutoCancel();
		wasOpen = false;
	}

	onDestroy(clearAutoCancel);

	const questionAllowsOther = (question: AskUserQuestion) => question.allow_other ?? allowOther;
	const hasOptions = (q: AskUserQuestion) => Array.isArray(q.options) && q.options.length > 0;
	const isMultiSelect = (q: AskUserQuestion) => Boolean(q?.multi_select);
	const isRecommendedOption = (q: AskUserQuestion, opt: AskUserOption, idx: number) =>
		opt.recommended ?? (q.show_recommended !== false && idx === 0);

	const focusInput = (node: HTMLInputElement, active: boolean) => {
		if (active) setTimeout(() => node.focus(), 0);
		return {
			update(next: boolean) {
				if (next) setTimeout(() => node.focus(), 0);
			}
		};
	};

	$: question = questions[questionIndex];
	$: selectedAnswer = question ? answers[question.id] : undefined;

	const isQuestionValid = (q: AskUserQuestion, selected = answers) => {
		const answer = selected[q.id];
		if (answer?.type === 'multi') {
			return answer.labels.length > 0 || (answer.other_text ?? '').trim() !== '';
		}
		return answer?.type === 'option' || (answer?.type === 'other' && answer.text.trim() !== '');
	};

	const hasAnswers = (selected = answers) =>
		questions.length > 0 && questions.every((q) => isQuestionValid(q, selected));

	$: complete = hasAnswers(answers);

	const submit = (selected = answers) => {
		if (!hasAnswers(selected)) {
			return;
		}

		const normalized: Record<string, DraftAnswer> = {};
		for (const question of questions) {
			const answer = selected[question.id];
			if (answer?.type === 'option') {
				normalized[question.id] = answer;
			} else if (answer?.type === 'other') {
				normalized[question.id] = {
					type: 'other',
					text: answer.text.trim()
				};
			} else if (answer?.type === 'multi') {
				const trimmedOther = (answer.other_text ?? '').trim();
				normalized[question.id] = {
					type: 'multi',
					option_indices: answer.option_indices,
					labels: answer.labels,
					...(trimmedOther ? { other_text: trimmedOther } : {})
				};
			}
		}

		show = false;
		dispatch('confirm', {
			status: 'answered',
			answers: normalized
		});
	};

	const advance = (selected = answers) => {
		if (questionIndex < questions.length - 1) {
			questionIndex += 1;
		} else if (hasAnswers(selected)) {
			submit(selected);
		}
	};

	const selectOption = (question: AskUserQuestion, option: AskUserOption, index: number) => {
		const selected: Record<string, DraftAnswer> = {
			...answers,
			[question.id]: {
				type: 'option',
				option_index: index,
				label: option.label,
				description: option.description ?? ''
			}
		};
		answers = selected;
		advance(selected);
	};

	const toggleMultiOption = (question: AskUserQuestion, option: AskUserOption, index: number) => {
		const existing = answers[question.id];
		const prevIndices = existing?.type === 'multi' ? existing.option_indices : [];
		const prevLabels = existing?.type === 'multi' ? existing.labels : [];
		const prevOther = existing?.type === 'multi' ? (existing.other_text ?? '') : '';

		const pos = prevIndices.indexOf(index);
		let nextIndices: number[];
		let nextLabels: string[];
		if (pos >= 0) {
			nextIndices = prevIndices.filter((_, i) => i !== pos);
			nextLabels = prevLabels.filter((_, i) => i !== pos);
		} else {
			nextIndices = [...prevIndices, index];
			nextLabels = [...prevLabels, option.label];
		}

		answers = {
			...answers,
			[question.id]: {
				type: 'multi',
				option_indices: nextIndices,
				labels: nextLabels,
				other_text: prevOther
			}
		};
	};

	const updateMultiOther = (question: AskUserQuestion, text: string) => {
		const existing = answers[question.id];
		answers = {
			...answers,
			[question.id]: {
				type: 'multi',
				option_indices: existing?.type === 'multi' ? existing.option_indices : [],
				labels: existing?.type === 'multi' ? existing.labels : [],
				other_text: text
			}
		};
	};

	const isMultiOptionChecked = (
		question: AskUserQuestion,
		index: number,
		selected = answers
	) => {
		const answer = selected[question.id];
		return answer?.type === 'multi' && answer.option_indices.includes(index);
	};

	const multiOtherText = (question: AskUserQuestion, selected = answers) => {
		const answer = selected[question.id];
		return answer?.type === 'multi' ? (answer.other_text ?? '') : '';
	};

	const selectOther = (question: AskUserQuestion) => {
		const existing = answers[question.id];
		answers = {
			...answers,
			[question.id]: {
				type: 'other',
				text: existing?.type === 'other' ? existing.text : ''
			}
		};
	};

	const updateOther = (question: AskUserQuestion, text: string) => {
		answers = {
			...answers,
			[question.id]: {
				type: 'other',
				text
			}
		};
	};

	const isSelectedOption = (question: AskUserQuestion, index: number, selected = answers) => {
		const answer = selected[question.id];
		return answer?.type === 'option' && answer.option_index === index;
	};

	const isSelectedOther = (question: AskUserQuestion, selected = answers) =>
		selected[question.id]?.type === 'other';

	const otherText = (question: AskUserQuestion, selected = answers) => {
		const answer = selected[question.id];
		return answer?.type === 'other' ? answer.text : '';
	};
</script>

{#if show}
	<section
		dir={$settings?.chatDirection ?? 'auto'}
		class="my-1 rounded-2xl bg-gray-50/70 px-3.5 py-3 dark:bg-white/[0.035]"
	>
		<div class="space-y-2">
			{#if question}
				{#key question.id}
					<div class="space-y-2">
						<div>
							<div class="flex items-center justify-between gap-3">
								<div class="flex items-baseline gap-1.5 text-sm font-medium text-gray-900 dark:text-gray-100">
									<span>{question.header}</span>
									{#if isMultiSelect(question)}
										<span class="text-[0.6875rem] font-normal text-gray-500 dark:text-gray-400">
											(Choix multiples)
										</span>
									{/if}
								</div>
								{#if questions.length > 1 || remainingSeconds !== null}
									<div
										class="flex shrink-0 items-center gap-2 text-right text-[0.6875rem] text-gray-500 dark:text-gray-400"
									>
										{#if remainingSeconds !== null}
											<span class="tabular-nums opacity-80">{remainingSeconds}s</span>
										{/if}
										{#if questions.length > 1}
											<span>{questionIndex + 1}/{questions.length}</span>
										{/if}
									</div>
								{/if}
							</div>
							<div class="mt-0.5 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
								{question.question}
							</div>
						</div>

						<div class="space-y-1">
							<div class="space-y-0.5">
								{#each question.options || [] as option, optionIndex}
									{@const multi = isMultiSelect(question)}
									{@const checked = multi
										? isMultiOptionChecked(question, optionIndex, answers)
										: isSelectedOption(question, optionIndex, answers)}
									<button
										type="button"
										role={multi ? 'checkbox' : undefined}
										aria-checked={multi ? checked : undefined}
										class="group flex w-full min-w-0 items-baseline gap-2 rounded-lg py-1.5 text-start transition-colors {checked
											? 'text-gray-950 dark:text-white'
											: 'text-gray-700 hover:text-gray-950 dark:text-gray-300 dark:hover:text-white'}"
										on:click={() =>
											multi
												? toggleMultiOption(question, option, optionIndex)
												: selectOption(question, option, optionIndex)}
									>
										{#if multi}
											<span
												aria-hidden="true"
												class="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center self-center rounded border transition-colors {checked
													? 'border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900'
													: 'border-gray-300 bg-transparent dark:border-gray-600'}"
											>
												{#if checked}
													<svg
														class="h-2.5 w-2.5"
														viewBox="0 0 12 12"
														fill="none"
														stroke="currentColor"
														stroke-width="2"
													>
														<path
															d="M2.5 6.2L5 8.7L9.5 3.5"
															stroke-linecap="round"
															stroke-linejoin="round"
														/>
													</svg>
												{/if}
											</span>
										{/if}
										<span class="min-w-0 shrink-0 text-xs">{option.label}</span>
										{#if option.description && option.description.trim() !== ''}
											<Tooltip
												as="span"
												className="min-w-0 flex-1"
												content={option.description}
												placement="top-start"
											>
												<span
													class="block truncate text-xs leading-relaxed text-gray-500 transition-colors group-hover:text-gray-700 dark:text-gray-400 dark:group-hover:text-gray-300"
												>
													{option.description}
												</span>
											</Tooltip>
										{/if}
										{#if isRecommendedOption(question, option, optionIndex)}
											<span
												class="shrink-0 rounded-full bg-gray-200/70 px-1.5 py-0.5 text-[0.625rem] text-gray-500 dark:bg-white/[0.08] dark:text-gray-400"
											>
												{$i18n.t('Recommended')}
											</span>
										{/if}
									</button>
								{/each}
							</div>

							{#if questionAllowsOther(question)}
								{#if hasOptions(question)}
									<div class="flex min-w-0 items-baseline gap-2 rounded-lg py-1.5">
										<div
											class="shrink-0 text-xs {(
												isMultiSelect(question)
													? multiOtherText(question, answers).trim() !== ''
													: isSelectedOther(question, answers)
											)
												? 'text-gray-950 dark:text-white'
												: 'text-gray-700 dark:text-gray-300'}"
										>
											{$i18n.t('Other')}
										</div>
										<input
											aria-label={$i18n.t('Other')}
											dir={$settings?.chatDirection ?? 'auto'}
											class="min-w-0 flex-1 bg-transparent text-xs text-gray-800 outline-hidden placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
											placeholder={$i18n.t('Type your answer')}
											value={isMultiSelect(question)
												? multiOtherText(question, answers)
												: otherText(question, answers)}
											on:focus={() => {
												if (!isMultiSelect(question)) {
													selectOther(question);
												}
											}}
											on:input={(event) => {
												const val = (event.currentTarget as HTMLInputElement).value;
												if (isMultiSelect(question)) {
													updateMultiOther(question, val);
												} else {
													updateOther(question, val);
												}
											}}
											on:keydown={(event) => {
												if (event.key === 'Enter') {
													event.preventDefault();
													if (isMultiSelect(question)) {
														if (isQuestionValid(question, answers)) {
															advance();
														}
													} else {
														advance();
													}
												}
											}}
										/>
									</div>
								{:else}
									<div class="py-1">
										<input
											aria-label={question.question}
											dir={$settings?.chatDirection ?? 'auto'}
											class="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-xs text-gray-800 outline-hidden placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
											placeholder={$i18n.t('Type your answer')}
											value={otherText(question)}
											use:focusInput={!hasOptions(question)}
											on:focus={() => selectOther(question)}
											on:input={(event) =>
												updateOther(question, (event.currentTarget as HTMLInputElement).value)}
											on:keydown={(event) => {
												if (event.key === 'Enter') {
													event.preventDefault();
													advance();
												}
											}}
										/>
									</div>
								{/if}
							{/if}
						</div>
					</div>
				{/key}
			{/if}

			<div class="flex items-center justify-between gap-2">
				<div class="flex items-center gap-1.5">
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal transition disabled:opacity-60 text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
						on:click={cancel}
					>
						{$i18n.t('Cancel')}
					</button>
					{#if questions.length > 1}
						<button
							type="button"
							class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal transition disabled:opacity-60 text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
							disabled={questionIndex === 0}
							on:click={() => (questionIndex -= 1)}
						>
							{$i18n.t('Previous')}
						</button>
					{/if}
				</div>
				{#if questionIndex < questions.length - 1}
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
						on:click={() => (questionIndex += 1)}
					>
						{$i18n.t('Next')}
					</button>
				{:else}
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white disabled:cursor-not-allowed"
						disabled={!complete}
						on:click={() => submit()}
					>
						{questions.length > 1 ? $i18n.t('Submit answers') : $i18n.t('Submit')}
					</button>
				{/if}
			</div>
		</div>
	</section>
{/if}
