<script lang="ts">
	import { createEventDispatcher, getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import ScheduleDropdown from '$lib/components/automations/ScheduleDropdown.svelte';
	import ModelDropdown from '$lib/components/automations/ModelDropdown.svelte';
	import DestinationDropdown from '$lib/components/automations/DestinationDropdown.svelte';
	import { getFolders } from '$lib/apis/folders';
	import { getChannels } from '$lib/apis/channels';
	import { channels, config, folders, models } from '$lib/stores';

	import {
		createAutomation,
		updateAutomationById,
		type AutomationForm,
		type AutomationResponse,
		type AutomationTarget,
		type AutomationTargetType
	} from '$lib/apis/automations';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let automation: AutomationResponse | null = null;
	export let cloneFrom: AutomationResponse | null = null;
	export let defaultTargetType: 'chat' | 'existing_chat' | 'persistent_chat' = 'chat';
	export let defaultChatId = '';
	export let defaultChatTitle = '';

	let name = '';
	let prompt = '';
	let model_id = '';
	let folder_id = '';
	let target_type: AutomationTargetType = 'chat';
	let channel_id = '';
	let chat_id = '';
	let chat_title = '';
	let chat_exists: boolean | null = null;
	let is_active = true;

	let loading = false;
	let foldersLoaded = false;
	let channelsLoaded = false;

	// Schedule dropdown ref
	let scheduleDropdown: ScheduleDropdown;

	const submitHandler = async () => {
		if (!name.trim() || !prompt.trim() || !model_id.trim()) {
			toast.error($i18n.t('Name, prompt, and model are required'));
			return;
		}
		if (target_type === 'channel' && !channel_id) {
			toast.error($i18n.t('Channel is required'));
			return;
		}
		if (target_type === 'existing_chat' && !chat_id) {
			toast.error($i18n.t('Select a chat'));
			return;
		}
		if (scheduleDropdown?.frequency === 'ONCE') {
			const scheduled = new Date(`${scheduleDropdown.onceDate}T${scheduleDropdown.onceTime}`);
			if (scheduled <= new Date()) {
				toast.error($i18n.t('Scheduled time must be in the future'));
				return;
			}
		}
		loading = true;
		try {
			const target: AutomationTarget =
				target_type === 'channel'
					? { type: 'channel', channel_id }
					: target_type === 'existing_chat'
						? { type: 'existing_chat', chat_id }
						: target_type === 'persistent_chat'
							? { type: 'persistent_chat', chat_id: chat_id || null }
							: { type: 'chat' };

			const form: AutomationForm = {
				name: name.trim(),
				folder_id:
					target_type === 'channel' || target_type === 'existing_chat' ? null : folder_id || null,
				data: {
					prompt: prompt.trim(),
					model_id: model_id.trim(),
					rrule: scheduleDropdown.buildRrule(),
					target
				},
				is_active
			};

			if (automation) {
				await updateAutomationById(localStorage.token, automation.id, form);
				toast.success($i18n.t('Automation updated'));
				show = false;
				dispatch('save', { id: automation.id });
			} else {
				const created = await createAutomation(localStorage.token, form);
				toast.success($i18n.t('Automation created'));
				show = false;
				dispatch('save', { id: created?.id });
			}
		} catch (e: any) {
			toast.error(e?.detail ?? `${e}` ?? 'Failed to save');
		} finally {
			loading = false;
		}
	};

	const init = async () => {
		await tick();
		if (!foldersLoaded && ($folders ?? []).length === 0) {
			const res = await getFolders(localStorage.token).catch(() => null);
			if (res) folders.set(res);
			foldersLoaded = true;
		}
		if ($config?.features?.enable_channels && !channelsLoaded && ($channels ?? []).length === 0) {
			const res = await getChannels(localStorage.token).catch(() => null);
			if (res) channels.set(res);
			channelsLoaded = true;
		}

		if (automation) {
			name = automation.name;
			prompt = automation.data.prompt;
			model_id = automation.data.model_id;
			folder_id = automation.folder_id ?? '';
			target_type = automation.data.target?.type ?? 'chat';
			channel_id = automation.data.target?.channel_id ?? '';
			chat_id = automation.data.target?.chat_id ?? '';
			chat_title = automation.target_chat_title ?? '';
			chat_exists = automation.target_chat_exists ?? null;
			is_active = automation.is_active;
			if (scheduleDropdown) {
				scheduleDropdown.parseRrule(automation.data.rrule);
			}
		} else if (cloneFrom) {
			name = cloneFrom.name;
			prompt = cloneFrom.data.prompt;
			model_id = cloneFrom.data.model_id;
			folder_id = ($folders ?? []).some((folder) => folder.id === cloneFrom.folder_id)
				? (cloneFrom.folder_id ?? '')
				: '';
			target_type = cloneFrom.data.target?.type ?? 'chat';
			channel_id = ($channels ?? []).some(
				(channel) => channel.id === cloneFrom.data.target?.channel_id
			)
				? (cloneFrom.data.target?.channel_id ?? '')
				: '';
			if (target_type === 'persistent_chat') {
				chat_id = '';
				chat_title = '';
				chat_exists = null;
			} else {
				chat_id = cloneFrom.data.target?.chat_id ?? '';
				chat_title = cloneFrom.target_chat_title ?? '';
				chat_exists = cloneFrom.target_chat_exists ?? null;
			}
			is_active = true;
			if (scheduleDropdown) {
				scheduleDropdown.parseRrule(cloneFrom.data.rrule);
			}
		} else {
			name = '';
			prompt = '';
			model_id = '';
			folder_id = '';
			target_type = defaultTargetType;
			channel_id = '';
			chat_id = defaultTargetType === 'existing_chat' ? defaultChatId : '';
			chat_title = defaultTargetType === 'existing_chat' ? defaultChatTitle : '';
			chat_exists = defaultTargetType === 'existing_chat' && defaultChatId ? true : null;
			is_active = true;

			if (!model_id) {
				const allModels = $models ?? [];
				const echoModel = allModels.find((m) => m.id === 'echo_orchestrator');
				if (echoModel) {
					model_id = echoModel.id;
				} else {
					const visibleModels = allModels.filter((m) => !(m?.info?.meta?.hidden ?? false));
					if (visibleModels.length === 1) {
						model_id = visibleModels[0].id;
					}
				}
			}
		}
	};

	$: if (show) {
		init();
	}
</script>

<Modal ariaLabel={name || $i18n.t('Automation')} size="md" bind:show>
	<div>
		<!-- Header -->
		<div class="flex justify-between dark:text-gray-100 px-4 pt-3 pb-1">
			<input
				class="w-full text-sm font-medium bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700"
				type="text"
				bind:value={name}
				aria-label={$i18n.t('Automation title')}
				placeholder={$i18n.t('Automation title')}
			/>
			<button
				class="self-center shrink-0 ml-2"
				aria-label={$i18n.t('Close')}
				on:click={() => (show = false)}
			>
				<XMark className="size-4" />
			</button>
		</div>

		<!-- Prompt -->
		<div class="px-5 pb-2">
			<label for="automation-instructions" class="block mb-1 text-xs text-gray-500"
				>{$i18n.t('Instructions')}</label
			>
			<textarea
				id="automation-instructions"
				class="w-full text-sm bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700 resize-none min-h-[12rem]"
				bind:value={prompt}
				rows={8}
				placeholder={$i18n.t('Enter prompt here.')}
			/>
		</div>

		<!-- Bottom toolbar -->
		<div
			class="flex flex-col sm:flex-row sm:items-center sm:justify-between px-4 pb-3.5 pt-1 gap-2"
		>
			<div class="flex items-center gap-0.5 flex-wrap min-w-0 sm:flex-1">
				<ScheduleDropdown bind:this={scheduleDropdown} side="top" align="start" />

				<ModelDropdown bind:model_id side="top" align="start" />

				<DestinationDropdown
					bind:target_type
					bind:channel_id
					bind:folder_id
					bind:chat_id
					bind:chat_title
					bind:chat_exists
					folders={$folders}
					channels={$channels}
					side="top"
					align="start"
				/>
			</div>

			<div class="flex items-center justify-end gap-2 shrink-0">
				<button
					class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60 dark:text-gray-400 dark:hover:bg-gray-850 dark:hover:text-white"
					type="button"
					on:click={() => (show = false)}
				>
					{$i18n.t('Cancel')}
				</button>
				<button
					class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white disabled:cursor-not-allowed"
					on:click={submitHandler}
					type="button"
					disabled={loading}
				>
					{automation ? $i18n.t('Save') : $i18n.t('Create')}
					{#if loading}
						<span class="shrink-0"><Spinner className="size-3" /></span>
					{/if}
				</button>
			</div>
		</div>
	</div>
</Modal>
