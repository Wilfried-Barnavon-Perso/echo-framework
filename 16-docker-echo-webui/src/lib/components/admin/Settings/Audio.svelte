<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { config } from '$lib/stores';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void = () => {};
	void saveHandler;

	$: audioConfig = ($config as any)?.audio ?? {};
	$: sttModel = audioConfig?.stt?.model || 'MODEL_ZEPHYR';
	$: ttsModel = audioConfig?.tts?.model || 'edge-tts';
	$: ttsVoice = audioConfig?.tts?.voice || 'fr-FR-VivienneMultilingualNeural';
	$: ttsSplitOn = audioConfig?.tts?.split_on || 'punctuation';
</script>

<div class="flex h-full flex-col justify-between text-sm">
	<div class="mb-4 flex items-center justify-between gap-2">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">
			{$i18n.t('settings.admin.audio.title')}
		</h2>
		<span
			class="shrink-0 rounded-lg bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600 dark:bg-gray-850 dark:text-gray-300"
		>
			Lecture seule — Aligné sur le moteur Audio ECHO
		</span>
	</div>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<AdminSettingSection title={$i18n.t('Voice calls')} first>
			<AdminSettingRow
				label="Mode d'appel vocal"
				description="Pipeline conversationnel unifié sans fournisseur tiers"
			>
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">
					Standard — /echo/v1/audio
				</span>
			</AdminSettingRow>
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t('settings.admin.audio.sections.speechToText.title')}>
			<AdminSettingRow
				label="Moteur STT"
				description="Transcription gérée nativement par le moteur ECHO"
			>
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">ECHO Audio</span>
			</AdminSettingRow>
			<AdminSettingRow label="Modèle STT">
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">{sttModel}</span>
			</AdminSettingRow>
			<AdminSettingRow label="Formats supportés">
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">
					WAV, MP3, WebM, OGG, M4A
				</span>
			</AdminSettingRow>
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t('settings.admin.audio.sections.textToSpeech.title')}>
			<AdminSettingRow
				label="Moteur TTS"
				description="Synthèse vocale locale sans clé API externe"
			>
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">ECHO Edge-TTS</span>
			</AdminSettingRow>
			<AdminSettingRow label="Modèle TTS">
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">{ttsModel}</span>
			</AdminSettingRow>
			<AdminSettingRow label="Voix par défaut">
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">{ttsVoice}</span>
			</AdminSettingRow>
			<AdminSettingRow label="Découpage TTS">
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">{ttsSplitOn}</span>
			</AdminSettingRow>
		</AdminSettingSection>
	</div>
</div>
