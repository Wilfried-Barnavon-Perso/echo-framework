<script lang="ts">
	import { createEventDispatcher, onMount, getContext } from 'svelte';
	import { getLanguages, changeLanguage } from '$lib/i18n';
	const dispatch = createEventDispatcher();

	import { config, theme } from '$lib/stores';
	import { setTheme } from '$lib/utils/theme';

	const i18n: any = getContext('i18n');

	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';

	export let saveSettings: Function;
	export let getModels: Function;
	void getModels;

	let selectedTheme = 'system';
	$: selectedTheme = $theme;

	let languages: Awaited<ReturnType<typeof getLanguages>> = [];
	let lang = $i18n.language;

	const saveHandler = async () => {
		try {
			await saveSettings({});
			dispatch('save');
		} catch {
			// The settings modal displays the save error; do not report success.
		}
	};

	onMount(async () => {
		languages = await getLanguages();

		if (!$config?.features?.enable_easter_eggs) {
			languages = languages.filter((l) => l.code !== 'dg-DG');
		}
	});
</script>

<div class="flex flex-col h-full justify-between text-sm" id="tab-general">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.general.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<UserSettingSection
			title={$i18n.t('settings.personal.general.sections.webuiSettings.title')}
			first
		>
			<UserSettingRow
				label={$i18n.t('settings.personal.general.theme.label')}
				description={$i18n.t('settings.personal.general.theme.description')}
			>
				<SettingsSelect
					bind:value={selectedTheme}
					ariaLabel={$i18n.t('settings.personal.general.theme.label')}
					placeholder={$i18n.t('Select a theme')}
					on:change={() => setTheme(selectedTheme)}
				>
					<option value="system">⚙️ {$i18n.t('System')}</option>
					<option value="dark">🌑 {$i18n.t('Dark')}</option>
					<option value="oled-dark">🌃 {$i18n.t('OLED Dark')}</option>
					<option value="light">☀️ {$i18n.t('Light')}</option>
					{#if $config?.features?.enable_easter_eggs}
						<option value="her">🌷 Her</option>
					{/if}
				</SettingsSelect>
			</UserSettingRow>

			<UserSettingRow
				label={$i18n.t('settings.personal.general.language.label')}
				description={$i18n.t('settings.personal.general.language.description')}
			>
				<SettingsSelect
					bind:value={lang}
					ariaLabel={$i18n.t('settings.personal.general.language.label')}
					placeholder={$i18n.t('Select a language')}
					on:change={() => {
						changeLanguage(lang);
					}}
				>
					{#each languages as language}
						<option value={language['code']}>{language['title']}</option>
					{/each}
				</SettingsSelect>
			</UserSettingRow>
		</UserSettingSection>
	</div>

	<div class="shrink-0 flex justify-end pt-3 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			on:click={() => {
				saveHandler();
			}}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</div>
