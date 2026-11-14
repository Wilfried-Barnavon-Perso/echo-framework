<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext } from 'svelte';

	import { settings, config } from '$lib/stores';
	import { getVoices as _getVoices } from '$lib/apis/audio';

	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UserSettingField from './UserSettingField.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';
	const dispatch = createEventDispatcher();

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	// Audio
	let speechAutoSend = false;
	let responseAutoPlayback = false;

	let STTLanguage = '';

	let voices = [];
	let voice = '';

	// Audio speed control
	let playbackRate = 1;
	const inputClass =
		'h-7 w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const getVoices = async () => {
		const res = await _getVoices(localStorage.token).catch((e) => {
			toast.error(`${e}`);
		});

		if (res) {
			voices = res.voices;
		}
	};

	const setResponseAutoPlayback = async (enabled: boolean) => {
		responseAutoPlayback = enabled;
		saveSettings({ responseAutoPlayback: responseAutoPlayback });
	};

	const setSpeechAutoSend = async (enabled: boolean) => {
		speechAutoSend = enabled;
		saveSettings({ speechAutoSend: speechAutoSend });
	};

	onMount(async () => {
		playbackRate = $settings.audio?.tts?.playbackRate ?? 1;
		speechAutoSend = $settings.speechAutoSend ?? false;
		responseAutoPlayback = $settings.responseAutoPlayback ?? false;

		STTLanguage = $settings?.audio?.stt?.language ?? '';

		if ($settings?.audio?.tts?.defaultVoice === $config?.audio?.tts?.voice) {
			voice = $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice ?? '';
		} else {
			voice = $config?.audio?.tts?.voice ?? '';
		}

		await getVoices();
	});
</script>

<form
	id="tab-audio"
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={async () => {
		saveSettings({
			audio: {
				stt: {
					language: STTLanguage !== '' ? STTLanguage : undefined
				},
				tts: {
					playbackRate: playbackRate,
					voice: voice !== '' ? voice : undefined,
					defaultVoice: $config?.audio?.tts?.voice ?? ''
				}
			}
		});
		dispatch('save');
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.personal.audio.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<UserSettingSection title={$i18n.t('settings.personal.audio.sections.sttSettings.title')} first>
			<UserSettingRow
				label={$i18n.t('settings.personal.audio.language.label')}
				description={$i18n.t('settings.personal.audio.language.description')}
			>
				<Tooltip
					content={$i18n.t(
						'The language of the input audio. Supplying the input language in ISO-639-1 (e.g. en) format will improve accuracy and latency. Leave blank to automatically detect the language.'
					)}
					placement="top"
				>
					<input
						type="text"
						bind:value={STTLanguage}
						aria-label={$i18n.t('Speech-to-Text Language')}
						placeholder={$i18n.t('e.g. en')}
						class="h-7 w-24 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500"
					/>
				</Tooltip>
			</UserSettingRow>

			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.audio.instantAutoSendAfterVoiceTranscription.label')}
				description={$i18n.t(
					'settings.personal.audio.instantAutoSendAfterVoiceTranscription.description'
				)}
			>
				<Switch
					ariaLabelledbyId={labelId}
					state={speechAutoSend}
					ariaLabel={$i18n.t(
						'settings.personal.audio.instantAutoSendAfterVoiceTranscription.label'
					)}
					on:change={(event) => {
						setSpeechAutoSend(event.detail);
					}}
				/>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.audio.sections.ttsSettings.title')}>
			<UserSettingRow
				let:labelId
				label={$i18n.t('settings.personal.audio.autoPlaybackResponse.label')}
				description={$i18n.t('settings.personal.audio.autoPlaybackResponse.description')}
			>
				<Switch
					ariaLabelledbyId={labelId}
					state={responseAutoPlayback}
					ariaLabel={$i18n.t('settings.personal.audio.autoPlaybackResponse.label')}
					on:change={(event) => {
						setResponseAutoPlayback(event.detail);
					}}
				/>
			</UserSettingRow>

			<UserSettingRow
				label={$i18n.t('settings.personal.audio.speechPlaybackSpeed.label')}
				description={$i18n.t('settings.personal.audio.speechPlaybackSpeed.description')}
			>
				<div class="relative flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-600">
					<input
						type="number"
						min="0"
						step="0.01"
						bind:value={playbackRate}
						aria-label={$i18n.t('settings.personal.audio.speechPlaybackSpeed.label')}
						class="h-7 w-16 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-right text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
					/>
					x
				</div>
			</UserSettingRow>
		</UserSettingSection>

		<UserSettingSection title={$i18n.t('settings.personal.audio.sections.voice.title')}>
			<UserSettingField
				label={$i18n.t('settings.personal.audio.serviceVoice.label')}
				description={$i18n.t('settings.personal.audio.serviceVoice.description')}
			>
				<input
					list="voice-list"
					class={inputClass}
					bind:value={voice}
					aria-label={$i18n.t('settings.personal.audio.sections.voice.title')}
					placeholder={$i18n.t('Select a voice')}
				/>

				<datalist id="voice-list">
					{#each voices as voice}
						<option value={voice.id}>{voice.name}</option>
					{/each}
				</datalist>
			</UserSettingField>
		</UserSettingSection>
	</div>

	<div class="shrink-0 flex justify-end text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
