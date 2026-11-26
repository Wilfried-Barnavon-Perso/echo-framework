<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { getPrompts } from '$lib/apis/prompts';
	import {
		getEchoSlashCommands,
		getEchoToken,
		type EchoSlashCommandItem
	} from '$lib/apis/echo';
	import {
		visibleControls
	} from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChatBubbleDotted from '$lib/components/icons/ChatBubbleDotted.svelte';
	import ChatBubbleDottedChecked from '$lib/components/icons/ChatBubbleDottedChecked.svelte';
	import Knobs from '$lib/components/icons/Knobs.svelte';
	import Annotation from '$lib/components/icons/Annotation.svelte';

	const i18n: any = getContext('i18n');

	export let query = '';
	export let onSelect: (e: any) => void = () => {};
	export let canComments = false;
	export let canFork = false;
	export let forkDisabled = false;
	export let canTemporary = false;
	export let temporaryEnabled = false;
	export let hasChatContent = false;
	void hasChatContent;

	let selectedIdx = 0;
	export let filteredItems: any[] = [];

	let prompts: any[] = [];
	let echoCommands: EchoSlashCommandItem[] = [];
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	const normalizeCmd = (s: string): string =>
		(s ?? '')
			.normalize('NFD')
			.replace(/[\u0300-\u036f]/g, '')
			.toLowerCase()
			.trim();

	const matchesCmd = (names: string[], q: string): boolean => {
		const nq = normalizeCmd(q);
		return !nq || names.some((name) => normalizeCmd(name).startsWith(nq));
	};

	onMount(async () => {
		try {
			const token = getEchoToken() || (typeof localStorage !== 'undefined' ? localStorage.token || '' : '');
			echoCommands = await getEchoSlashCommands(token);
		} catch {
			echoCommands = [];
		}
	});

	$: commandItems = [
		...(canTemporary && matchesCmd(['temporary'], query)
			? [{ type: 'command', data: { id: 'temporary' } }]
			: []),
		...(canFork && $visibleControls['chat.fork'] && matchesCmd(['fork', 'dupliquer'], query)
			? [{ type: 'command', data: { id: 'fork' } }]
			: []),
		...(canComments && matchesCmd(['comments'], query)
			? [{ type: 'command', data: { id: 'comments' } }]
			: []),
		...($visibleControls['chat.settings_command'] &&
		matchesCmd(['settings', 'reglages', 'réglages'], query)
			? [{ type: 'command', data: { id: 'settings' } }]
			: [])
	];

	$: filteredEchoCommands = echoCommands.filter((c) =>
		matchesCmd([c.id, c.slash.replace(/^\//, ''), ...(c.aliases ?? [])], query)
	);

	$: filteredPrompts = prompts
		.filter((p) => p.command.toLowerCase().includes(query.toLowerCase()))
		.sort((a, b) => a.name.localeCompare(b.name));

	$: filteredItems = [
		...commandItems,
		...filteredEchoCommands.map((data) => ({ type: 'echo_command', data })),
		...filteredPrompts.map((data) => ({ type: 'prompt', data }))
	];

	$: if (query) {
		selectedIdx = 0;
	}

	$: selectedIdx = Math.min(selectedIdx, Math.max(filteredItems.length - 1, 0));

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
		}, 200);
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	const getItems = async () => {
		const promptRes = await getPrompts(localStorage.token).catch(() => null);

		if (promptRes) {
			prompts = promptRes;
		}
	};

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	export const select = async () => {
		const item = filteredItems[selectedIdx];
		if (item?.type === 'command' && item.data?.id === 'fork' && forkDisabled) {
			return;
		}
		if (item) {
			onSelect(item);
		}
	};
</script>

{#if commandItems.length > 0}
	<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
		{$i18n.t('Commands')}
	</div>

	{#each commandItems as item, commandIdx}
		{#if item.data.id === 'temporary'}
			<Tooltip content={$i18n.t('Toggle temporary chat for this new chat.')} placement="top">
				<button
					type="button"
					aria-label={$i18n.t('Temporary: toggle temporary chat for this new chat.')}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-[0.8125rem] text-left transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40
						{commandIdx === selectedIdx ? 'app-interactive-active selected-command-option-button' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						{#if temporaryEnabled}
							<ChatBubbleDottedChecked className="size-3.5" strokeWidth="1.6" />
						{:else}
							<ChatBubbleDotted className="size-3.5" strokeWidth="1.6" />
						{/if}
					</span>
					<span class="flex-1 min-w-0 flex items-center gap-2 overflow-hidden">
						<span class="min-w-0 flex-1 truncate">{$i18n.t('Temporary')}</span>
						<span class="app-muted max-w-24 shrink-0 truncate text-[0.625rem]">
							{temporaryEnabled ? $i18n.t('On') : $i18n.t('Off')}
						</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'fork'}
			<Tooltip content={$i18n.t('Fork the current chat branch into a new chat.')} placement="top">
				<button
					type="button"
					aria-label={$i18n.t('Fork: fork the current chat branch into a new chat.')}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-[0.8125rem] text-left transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40
						{commandIdx === selectedIdx
						? 'app-interactive-active selected-command-option-button'
						: ''} disabled:opacity-50"
					disabled={forkDisabled}
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						if (!forkDisabled) {
							onSelect(item);
						}
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M4 12H9" />
							<path d="M9 12C12.5 12 12.5 7 16 7H20" />
							<path d="M17 4L20 7L17 10" />
							<path d="M9 12C12.5 12 12.5 17 16 17H20" />
							<path d="M17 14L20 17L17 20" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-center gap-2 overflow-hidden">
						<span class="min-w-0 flex-1 truncate">{$i18n.t('Fork')}</span>
						<span class="app-muted max-w-24 shrink-0 truncate text-[0.625rem]">
							{$i18n.t('Current branch')}
						</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'comments'}
			<button
				type="button"
				aria-label={$i18n.t('Comments')}
				class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-[0.8125rem] text-left transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40
					{commandIdx === selectedIdx ? 'app-interactive-active selected-command-option-button' : ''}"
				on:mousedown={(e) => e.preventDefault()}
				on:click={() => onSelect(item)}
				on:mouseenter={() => (selectedIdx = commandIdx)}
				on:focus={() => {}}
				data-selected={commandIdx === selectedIdx}
			>
				<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
					<Annotation className="size-3.5" />
				</span>
				<span class="flex-1 min-w-0 flex items-center gap-2 overflow-hidden">
					<span class="min-w-0 flex-1 truncate">{$i18n.t('Comments')}</span>
					<span class="app-muted max-w-24 shrink-0 truncate text-[0.625rem]">/{item.data.id}</span>
				</span>
			</button>
		{:else if item.data.id === 'settings'}
			<Tooltip content={$i18n.t('Open settings.')} placement="top">
				<button
					type="button"
					aria-label={$i18n.t('Settings: open settings.')}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-[0.8125rem] text-left transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40
						{commandIdx === selectedIdx ? 'app-interactive-active selected-command-option-button' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Knobs className="size-3.5" />
					</span>
					<span class="flex-1 min-w-0 flex items-center gap-2 overflow-hidden">
						<span class="min-w-0 flex-1 truncate">{$i18n.t('Settings')}</span>
						<span class="app-muted max-w-24 shrink-0 truncate text-[0.625rem]">/settings</span>
					</span>
				</button>
			</Tooltip>
		{/if}
	{/each}
{/if}

{#if filteredEchoCommands.length > 0}
	<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
		Commandes Écho
	</div>

	{#each filteredEchoCommands as cmd, echoIdx}
		{@const itemIdx = commandItems.length + echoIdx}
		<button
			type="button"
			class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-[0.8125rem] text-left transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40 {selectedIdx ===
			itemIdx
				? 'app-interactive-active selected-command-option-button'
				: ''}"
			on:mousedown={(e) => e.preventDefault()}
			on:click={() => onSelect({ type: 'echo_command', data: cmd })}
			on:mousemove={() => (selectedIdx = itemIdx)}
			on:focus={() => {}}
			data-selected={selectedIdx === itemIdx}
		>
			<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
				<svg
					class="size-3.5"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.75"
					stroke-linecap="round"
					stroke-linejoin="round"
					aria-hidden="true"
				>
					<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" />
				</svg>
			</span>
			<span class="flex-1 min-w-0 flex items-center justify-between gap-2 overflow-hidden">
				<span class="shrink-0 font-medium">{cmd.slash}</span>
				<span class="text-xs text-gray-500 dark:text-gray-400 truncate">
					{cmd.ui_description}
				</span>
			</span>
		</button>
	{/each}
{/if}

{#if filteredPrompts.length > 0}
	<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
		{$i18n.t('Prompts')}
	</div>

	{#each filteredPrompts as promptItem, promptIdx}
		{@const itemIdx = commandItems.length + filteredEchoCommands.length + promptIdx}
		<Tooltip content={promptItem.name} placement="top-start">
			<button
				class="flex h-6 w-full items-center gap-2 rounded-xl px-2 text-left text-[0.8125rem] transition-colors duration-75 hover:bg-gray-50/40 dark:hover:bg-gray-800/40 {itemIdx ===
				selectedIdx
					? 'app-interactive-active selected-command-option-button'
					: ''}"
				type="button"
				on:click={() => {
					onSelect({ type: 'prompt', data: promptItem });
				}}
				on:mousemove={() => {
					selectedIdx = itemIdx;
				}}
				on:focus={() => {}}
				data-selected={itemIdx === selectedIdx}
			>
				<span class="min-w-0 flex-1 truncate font-normal">
					{promptItem.command}
				</span>

				<span class="app-muted max-w-24 shrink-0 truncate text-[0.625rem]">
					{promptItem.name}
				</span>
			</button>
		</Tooltip>
	{/each}
{/if}
