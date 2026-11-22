<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';

	import { getBackendConfig } from '$lib/apis';
	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import { getBanners, setBanners } from '$lib/apis/configs';
	import InterfaceSettings from '$lib/components/common/InterfaceSettings.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import LanguageModeSelect from '$lib/components/common/LanguageModeSelect.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import BuildVersion from '$lib/components/common/BuildVersion.svelte';
	import { WEBUI_VERSION } from '$lib/constants';
	import { banners as _banners, config } from '$lib/stores';
	import type { Banner } from '$lib/types';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Banners from './Interface/Banners.svelte';
	import Events from './Events.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import I18nSettings from './I18nSettings.svelte';
	import { updateI18n } from '$lib/i18n';
	import { entriesToI18n, i18nToEntries, type I18nEntry } from '$lib/utils/translationDictionary';

	const i18n: any = getContext('i18n');

	export let saveHandler: Function;

	let adminConfig: any = null;
	let defaultInterfaceSettings: Record<string, any> = {};
	let showUserUiDefaults = false;
	let uiI18nEntries: I18nEntry[] = [];
	let saving = false;

	let banners: Banner[] = [];
	let bannerLocale = '';
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const updateBanners = async () => {
		_banners.set(await setBanners(localStorage.token, banners));
	};

	const saveDefaultInterfaceSettings = (updated: Record<string, any>) => {
		defaultInterfaceSettings = { ...defaultInterfaceSettings, ...updated };
	};

	const getDefaultInterfaceSettings = () => {
		const value = adminConfig?.DEFAULT_INTERFACE_SETTINGS;
		return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
	};

	const updateHandler = async () => {
		if (saving) return;
		saving = true;
		try {
			const cleaned = entriesToI18n(uiI18nEntries);
			const res = await updateAdminConfig(localStorage.token, {
				...adminConfig,
				SKILL_CONTENT_MAX_CHARS: adminConfig.SKILL_CONTENT_MAX_CHARS ?? null,
				SKILL_PREVIEW_MAX_CHARS: adminConfig.SKILL_PREVIEW_MAX_CHARS ?? null,
				VIEW_CHAT_MAX_CHARS: adminConfig.VIEW_CHAT_MAX_CHARS ?? null,
				DEFAULT_INTERFACE_SETTINGS: defaultInterfaceSettings,
				I18N: cleaned
			});
			if (!res) throw new Error($i18n.t('Failed to update settings'));
			if (res.sessions_revoked) {
				localStorage.removeItem('token');
				window.location.href = '/auth?state=logout&form=signin';
				return;
			}
			await updateI18n(res.I18N ?? cleaned);
			await updateBanners();
			await config.set(await getBackendConfig());
			saveHandler();
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: Array.isArray(error)
						? error.map((entry) => entry.msg).join('\n')
						: String(error)
			);
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		adminConfig = await getAdminConfig(localStorage.token);
		defaultInterfaceSettings = getDefaultInterfaceSettings();
		uiI18nEntries = i18nToEntries(adminConfig.I18N ?? {});

		banners = [...$_banners];
	});
</script>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={async () => {
		updateHandler();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.admin.general.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if adminConfig !== null}
			<AdminSettingSection first>
				<div class="flex flex-col gap-2 text-xs">
					<div class="flex items-center justify-between gap-4">
						<span class="text-gray-600 dark:text-gray-400">ECHO Framework</span>
						<span class="font-mono text-gray-700 dark:text-gray-200">
							v{$config?.echo_version ?? WEBUI_VERSION}
						</span>
					</div>

					<div class="flex items-center justify-between gap-4">
						<span class="text-gray-600 dark:text-gray-400">ECHO-UI</span>
						<span class="font-mono text-gray-700 dark:text-gray-200">
							<BuildVersion />
						</span>
					</div>

					<div class="flex items-center justify-between gap-4">
						<span class="text-gray-600 dark:text-gray-400">Import de fichiers</span>
						<span class="font-mono text-gray-700 dark:text-gray-200">
							4 Go max (flux zéro-RAM ECHO)
						</span>
					</div>

					<div class="pt-1 text-gray-600 dark:text-gray-400">
						<div class="font-medium text-gray-700 dark:text-gray-200">Double Licence</div>
						<div class="mt-0.5 text-gray-500 dark:text-gray-400">
							ECHO Apache 2.0 (Copyright © 2025-2026 Wilfried BARNAVON) + Open WebUI License
							(forké depuis Open WebUI v0.13.2 — Clause 4 : ≤ 50 utilisateurs / 30 jours glissants ; suppression du seuil avec ECHO-UI soumise à un accord officiel avec Open WebUI Inc.).
						</div>
					</div>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('settings.admin.general.sections.features.title')}>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.folders.label')}
					description={$i18n.t('settings.admin.general.folders.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_FOLDERS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.comments.label')}
					description={$i18n.t('settings.admin.general.comments.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_COMMENTS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.notifications.label')}
					description={$i18n.t('settings.admin.general.notifications.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_NOTIFICATIONS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.channels.label')}
					description={$i18n.t('settings.admin.general.channels.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CHANNELS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				{#if adminConfig.ENABLE_CHANNELS}
					<AdminSettingRow
						label={$i18n.t('settings.admin.general.encryptedChannels.label')}
						description={$i18n.t('settings.admin.general.encryptedChannels.description')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<Switch bind:state={adminConfig.ENABLE_ENCRYPTED_CHANNELS} ariaLabelledbyId={labelId} />
					</AdminSettingRow>
					<AdminSettingRow
						label={$i18n.t('settings.admin.general.modelResponseMode.label')}
						description={$i18n.t('settings.admin.general.modelResponseMode.description')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<SettingsSelect
							bind:value={adminConfig.CHANNEL_MODEL_RESPONSE_MODE}
							aria-labelledby={labelId}
						>
							<option value="thread">{$i18n.t('Thread')}</option>
							<option value="channel">{$i18n.t('Channel')}</option>
						</SettingsSelect>
					</AdminSettingRow>
				{/if}
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.calendar.label')}
					description={$i18n.t('settings.admin.general.calendar.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CALENDAR} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.automations.label')}
					description={$i18n.t('settings.admin.general.automations.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_AUTOMATIONS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.userWebhooks.label')}
					description={$i18n.t('settings.admin.general.userWebhooks.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_WEBHOOKS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.userStatus.label')}
					description={$i18n.t('settings.admin.general.userStatus.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_STATUS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingField
					label={$i18n.t('settings.admin.general.responseWatermark.label')}
					description={$i18n.t('settings.admin.general.responseWatermark.description')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t('Enter a watermark for the response. Leave empty for none.')}
						bind:value={adminConfig.RESPONSE_WATERMARK}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('settings.admin.general.webuiUrl.label')}
					description={$i18n.t('settings.admin.general.webuiUrl.description')}
					forId="webui-url"
				>
					<input
						id="webui-url"
						class={inputClass}
						type="text"
						placeholder={`e.g.) "http://localhost:3000"`}
						bind:value={adminConfig.WEBUI_URL}
					/>
				</AdminSettingField>
			</AdminSettingSection>

			<Events />

			<AdminSettingSection title={$i18n.t('settings.admin.general.sections.ui.title')}>
				<fieldset id="ui-i18n-settings" disabled={saving} class="min-w-0">
					<I18nSettings bind:entries={uiI18nEntries} />
				</fieldset>
				<div class="shrink-0">
					<div class="flex items-center justify-between gap-4 py-0.5">
						<button
							class="min-w-0 flex-1 text-left text-xs text-gray-600 transition hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
							type="button"
							on:click={() => {
								showUserUiDefaults = !showUserUiDefaults;
							}}
						>
							<div>{$i18n.t('Default Interface Settings')}</div>
							<div class="mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
								{$i18n.t(
									'Set system-wide interface defaults for every account. Personal settings override these defaults.'
								)}
							</div>
						</button>

						<button
							class="shrink-0 text-[0.6875rem] text-gray-400 transition hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							type="button"
							on:click={() => {
								showUserUiDefaults = !showUserUiDefaults;
							}}
						>
							{showUserUiDefaults ? $i18n.t('Close') : $i18n.t('Configure')}
						</button>
					</div>

					{#if showUserUiDefaults}
						<div class="mt-0.5 space-y-2">
							<div class="flex items-center justify-between gap-4 py-0.5">
								<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
									{Object.keys(defaultInterfaceSettings).length}
									{$i18n.t('settings configured')}
								</div>

								{#if Object.keys(defaultInterfaceSettings).length > 0}
									<button
										class="shrink-0 text-[0.6875rem] text-gray-400 transition hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
										type="button"
										on:click={() => {
											defaultInterfaceSettings = {};
										}}
									>
										{$i18n.t('Clear')}
									</button>
								{/if}
							</div>

							<div class="max-h-[28rem] overflow-y-auto pb-2 pr-1 scrollbar-hover">
								<InterfaceSettings
									settingsValue={defaultInterfaceSettings}
									saveSettings={saveDefaultInterfaceSettings}
								/>
							</div>
						</div>
					{/if}
				</div>

				<div>
					<div class="mb-1 flex min-h-7 w-full items-center justify-between gap-2">
						<div class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('settings.admin.general.banners.label')}
						</div>
						<div class="flex shrink-0 items-center gap-1">
							{#if banners.length > 0}
								<LanguageModeSelect bind:value={bannerLocale} className="w-fit" />
							{/if}
							<button
								class="flex size-6 items-center justify-center text-gray-400 dark:text-gray-600"
								type="button"
								aria-label={$i18n.t('settings.admin.general.addBanner.label')}
								on:click={() => {
									if (banners.length === 0 || banners[banners.length - 1]?.content !== '') {
										banners = [
											...banners,
											{
												id: uuidv4(),
												type: '',
												title: '',
												content: '',
												dismissible: true,
												timestamp: Math.floor(Date.now() / 1000)
											}
										];
									}
								}}
							>
								<Plus />
							</button>
						</div>
					</div>
					<div class="mb-2 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t('Create announcements shown to users in the app.')}
					</div>
					<Banners bind:banners locale={bannerLocale} />
				</div>
			</AdminSettingSection>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
			disabled={saving}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
