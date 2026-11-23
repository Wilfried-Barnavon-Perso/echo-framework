<script lang="ts">
	import { onDestroy, onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getEchoToken } from '$lib/apis/echo';
	import {
		cancelLlmOAuthFlow,
		deleteLlmApiKey,
		getLlmAuthStatus,
		resetLlmAuth,
		saveLlmApiKey,
		startLlmOAuthFlow,
		type ApiKeySlot,
		type LlmAuthStatusResponse
	} from '$lib/apis/echo/llm_auth';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	import UserSettingField from './UserSettingField.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n: any = getContext('i18n');

	let loading = true;
	let refreshing = false;
	let startingOAuth = false;
	let cancellingOAuth = false;
	let savingPrimary = false;
	let savingSecondary = false;
	let resetting = false;
	let showResetConfirm = false;

	let primaryKeyInput = '';
	let secondaryKeyInput = '';

	let statusData: LlmAuthStatusResponse | null = null;
	let localExpiresIn = 0;

	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let countdownTimer: ReturnType<typeof setInterval> | null = null;
	let wasFlowActive = false;

	const inputClass =
		'h-7 w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const primaryBtnClass =
		'flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white';
	const secondaryBtnClass =
		'flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 text-xs font-normal text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-white/[0.08] dark:bg-white/[0.03] dark:text-gray-300 dark:hover:bg-white/[0.06]';
	const dangerBtnClass =
		'flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-red-600 px-2.5 text-xs font-normal text-white transition hover:bg-red-700 disabled:opacity-60 dark:bg-red-500/90 dark:hover:bg-red-500';

	const stopPolling = () => {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
		if (countdownTimer) {
			clearInterval(countdownTimer);
			countdownTimer = null;
		}
	};

	const startPolling = () => {
		if (!pollTimer) {
			pollTimer = setInterval(() => {
				void fetchStatus(false, true);
			}, 2000);
		}
		if (!countdownTimer) {
			countdownTimer = setInterval(() => {
				if (localExpiresIn > 0) {
					localExpiresIn -= 1;
				}
			}, 1000);
		}
	};

	const copyText = async (text: string, successLabel: string) => {
		try {
			await navigator.clipboard.writeText(text);
			toast.success(successLabel);
		} catch {
			toast.error($i18n.t('Failed to copy to clipboard'));
		}
	};

	const formatFractionPercent = (fraction: number | null | undefined): number => {
		if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return 0;
		return Math.max(0, Math.min(100, Math.round(fraction * 100)));
	};

	const formatCountdown = (seconds: number): string => {
		const s = Math.max(0, Math.floor(seconds));
		const min = Math.floor(s / 60);
		const rem = s % 60;
		return `${min}:${rem.toString().padStart(2, '0')}`;
	};

	const fetchStatus = async (refresh = false, silent = false) => {
		if (refresh) refreshing = true;
		if (!silent && !statusData) loading = true;

		try {
			const next = await getLlmAuthStatus(getEchoToken(), refresh);
			statusData = next;
			const isActive = Boolean(next.pkce_flow?.active);

			if (isActive) {
				localExpiresIn = next.pkce_flow.expires_in_sec;
				wasFlowActive = true;
				startPolling();
			} else {
				if (wasFlowActive) {
					wasFlowActive = false;
					stopPolling();
					const res = next.pkce_flow?.last_result;
					if (res) {
						if (res.success) {
							toast.success(res.message);
						} else {
							toast.error(res.message);
						}
					}
				} else {
					stopPolling();
				}
			}
		} catch (err) {
			if (!silent) {
				toast.error(`${err}`);
			}
		} finally {
			loading = false;
			refreshing = false;
		}
	};

	const handleStartOAuth = async () => {
		startingOAuth = true;
		try {
			const res = await startLlmOAuthFlow(getEchoToken());
			if (statusData) {
				statusData = { ...statusData, pkce_flow: res.pkce_flow };
			} else {
				await fetchStatus(false, true);
			}
			localExpiresIn = res.pkce_flow.expires_in_sec;
			wasFlowActive = true;
			startPolling();
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			startingOAuth = false;
		}
	};

	const handleCancelOAuth = async () => {
		cancellingOAuth = true;
		try {
			await cancelLlmOAuthFlow(getEchoToken());
			wasFlowActive = false;
			stopPolling();
			await fetchStatus(false, true);
			toast.info($i18n.t('OAuth2 connection cancelled'));
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			cancellingOAuth = false;
		}
	};

	const handleSaveKey = async (slot: ApiKeySlot) => {
		const raw = (slot === 'primary' ? primaryKeyInput : secondaryKeyInput).trim();
		if (!raw) return;

		if (slot === 'primary') savingPrimary = true;
		else savingSecondary = true;

		try {
			await saveLlmApiKey(getEchoToken(), raw, slot);
			if (slot === 'primary') primaryKeyInput = '';
			else secondaryKeyInput = '';
			await fetchStatus(false, true);
			toast.success($i18n.t('API key validated and saved'));
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			if (slot === 'primary') savingPrimary = false;
			else savingSecondary = false;
		}
	};

	const handleDeleteKey = async (slot: ApiKeySlot) => {
		try {
			await deleteLlmApiKey(getEchoToken(), slot);
			await fetchStatus(false, true);
			toast.success($i18n.t('API key deleted'));
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const handleResetAuth = async () => {
		resetting = true;
		try {
			wasFlowActive = false;
			stopPolling();
			await resetLlmAuth(getEchoToken());
			await fetchStatus(false, true);
			toast.success($i18n.t('LLM authentication has been reset'));
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			resetting = false;
		}
	};

	onMount(() => {
		void fetchStatus(false, false);
	});

	onDestroy(() => {
		stopPolling();
	});
</script>

<div id="tab-llm-auth" class="flex h-full flex-col text-sm">
	<div class="flex items-center justify-between gap-2 pb-2">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">
			{$i18n.t('settings.personal.llm_auth.title')}
		</h2>
		<button
			type="button"
			class={secondaryBtnClass}
			disabled={loading || refreshing}
			on:click={() => fetchStatus(true, false)}
		>
			{#if refreshing}
				<Spinner className="size-3.5" />
			{/if}
			<span>{$i18n.t('Refresh')}</span>
		</button>
	</div>

	<div class="flex-1 min-h-0 w-full overflow-y-auto scrollbar-hover pr-1.5">
		{#if loading && !statusData}
			<div class="flex h-40 items-center justify-center">
				<Spinner className="size-5" />
			</div>
		{:else if statusData}
			{@const snap = statusData.snapshot}
			{@const flow = statusData.pkce_flow}

			<UserSettingSection first title={$i18n.t('settings.personal.llm_auth.statusSection.title')}>
				<div class="flex flex-col gap-2 rounded-xl border border-gray-100/70 bg-gray-50/40 p-3 dark:border-white/[0.06] dark:bg-white/[0.02]">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex flex-col gap-0.5">
							<div class="flex items-center gap-2 text-xs font-medium text-gray-900 dark:text-gray-100">
								<span>{snap.user_email || $i18n.t('No Google account connected')}</span>
								{#if snap.user_tier}
									<span class="rounded-md bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
										{snap.user_tier}
									</span>
								{/if}
							</div>
							<div class="text-[11px] text-gray-500 dark:text-gray-400">
								{$i18n.t('Active sources')}:
								{snap.auth_sources?.length ? snap.auth_sources.join(' > ') : $i18n.t('None')}
								{#if snap.project_id}
									· Project: {snap.project_id}
								{/if}
								{#if snap.credits_val && snap.credits_val !== '0'}
									· {$i18n.t('Credits')}: {snap.credits_val}
								{/if}
							</div>
						</div>
						<span
							class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium {statusData.oauth_connected
								? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
								: 'bg-gray-200/70 text-gray-600 dark:bg-white/[0.06] dark:text-gray-400'}"
						>
							<span
								class="size-1.5 rounded-full {statusData.oauth_connected ? 'bg-emerald-500' : 'bg-gray-400'}"
							></span>
							{statusData.oauth_connected ? $i18n.t('OAuth2 Active') : $i18n.t('OAuth2 Inactive')}
						</span>
					</div>

					{#if statusData.oauth_connected}
						<div class="grid grid-cols-1 gap-2 pt-1 sm:grid-cols-2">
							{#if snap.quota_5h_fraction !== null}
								{@const pct5h = formatFractionPercent(snap.quota_5h_fraction)}
								<div class="flex flex-col gap-1 rounded-lg bg-white/70 p-2 dark:bg-black/20">
									<div class="flex items-center justify-between text-[11px]">
										<span class="text-gray-600 dark:text-gray-300">
											{$i18n.t('5h Quota Window')}
											{#if snap.quota_5h_disabled}({$i18n.t('Disabled')}){/if}
										</span>
										<span class="font-medium tabular-nums">{pct5h}%</span>
									</div>
									<div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-white/10">
										<div class="h-full rounded-full bg-blue-500 transition-all" style="width: {pct5h}%"></div>
									</div>
									<div class="text-[10px] text-gray-400">
										{$i18n.t('Reset')}: {snap.quota_5h_reset}
									</div>
								</div>
							{/if}

							{#if snap.quota_weekly_fraction !== null}
								{@const pctW = formatFractionPercent(snap.quota_weekly_fraction)}
								<div class="flex flex-col gap-1 rounded-lg bg-white/70 p-2 dark:bg-black/20">
									<div class="flex items-center justify-between text-[11px]">
										<span class="text-gray-600 dark:text-gray-300">{$i18n.t('Weekly Quota Window')}</span>
										<span class="font-medium tabular-nums">{pctW}%</span>
									</div>
									<div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-white/10">
										<div class="h-full rounded-full bg-indigo-500 transition-all" style="width: {pctW}%"></div>
									</div>
									<div class="text-[10px] text-gray-400">
										{$i18n.t('Reset')}: {snap.quota_weekly_reset}
									</div>
								</div>
							{/if}
						</div>

						{#if snap.quota_5h_fraction === null && snap.quota_weekly_fraction === null}
							{@const pctGen = formatFractionPercent(snap.quota_fraction)}
							<div class="flex flex-col gap-1 rounded-lg bg-white/70 p-2 dark:bg-black/20">
								<div class="flex items-center justify-between text-[11px]">
									<span class="text-gray-600 dark:text-gray-300">
										{snap.quota_model} ({snap.quota_type})
									</span>
									<span class="font-medium tabular-nums">{pctGen}%</span>
								</div>
								<div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-white/10">
									<div class="h-full rounded-full bg-blue-500 transition-all" style="width: {pctGen}%"></div>
								</div>
								<div class="text-[10px] text-gray-400">
									{$i18n.t('Reset')}: {snap.quota_reset}
								</div>
							</div>
						{/if}
					{/if}
				</div>
			</UserSettingSection>

			<UserSettingSection title={$i18n.t('settings.personal.llm_auth.oauthSection.title')}>
				{#if !flow.active}
					<UserSettingRow
						label={$i18n.t('settings.personal.llm_auth.oauthConnect.label')}
						description={$i18n.t('settings.personal.llm_auth.oauthConnect.description')}
					>
						<button
							type="button"
							class={primaryBtnClass}
							disabled={startingOAuth}
							on:click={handleStartOAuth}
						>
							{#if startingOAuth}
								<Spinner className="size-3.5" />
							{/if}
							<span>
								{statusData.oauth_connected
									? $i18n.t('Reconnect Google (OAuth2)')
									: $i18n.t('Connect Google (OAuth2)')}
							</span>
						</button>
					</UserSettingRow>
				{:else}
					<div class="flex flex-col gap-3 rounded-xl border border-blue-500/25 bg-blue-500/[0.04] p-3">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<div class="flex items-center gap-2 text-xs font-medium text-gray-900 dark:text-gray-100">
								<span
									class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] {flow.ssh_tunnel_connected
										? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
										: 'bg-amber-500/15 text-amber-700 dark:text-amber-400'}"
								>
									<span
										class="size-1.5 rounded-full {flow.ssh_tunnel_connected
											? 'bg-emerald-500'
											: 'bg-amber-500 animate-pulse'}"
									></span>
									{flow.ssh_tunnel_connected
										? $i18n.t('SSH Tunnel Connected')
										: $i18n.t('Waiting for SSH Tunnel...')}
								</span>
								<span class="text-[11px] tabular-nums text-gray-500 dark:text-gray-400">
									{$i18n.t('Expires in')} {formatCountdown(localExpiresIn)}
								</span>
							</div>

							<button
								type="button"
								class={secondaryBtnClass}
								disabled={cancellingOAuth}
								on:click={handleCancelOAuth}
							>
								{$i18n.t('Cancel')}
							</button>
						</div>

						<div class="flex flex-col gap-1.5">
							<div class="text-xs font-medium text-gray-800 dark:text-gray-200">
								1. {$i18n.t('Run this SSH command in your terminal')}
							</div>
							<div class="flex items-center gap-2">
								<code class="flex-1 overflow-x-auto rounded-lg border border-gray-200/70 bg-white px-2.5 py-1.5 font-mono text-[11px] text-gray-800 dark:border-white/[0.08] dark:bg-black/30 dark:text-gray-200">
									{flow.ssh_command}
								</code>
								<button
									type="button"
									class={secondaryBtnClass}
									on:click={() => copyText(flow.ssh_command, $i18n.t('SSH command copied'))}
								>
									{$i18n.t('Copy')}
								</button>
							</div>
							<div class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
								<span>{$i18n.t('Temporary password')}:</span>
								<code class="rounded bg-gray-200/70 px-1.5 py-0.5 font-mono text-[11px] dark:bg-white/10">
									{flow.temp_password}
								</code>
								<button
									type="button"
									class={secondaryBtnClass}
									on:click={() => copyText(flow.temp_password, $i18n.t('Password copied'))}
								>
									{$i18n.t('Copy')}
								</button>
							</div>
						</div>

						<div class="flex flex-col gap-1.5">
							<div class="text-xs font-medium text-gray-800 dark:text-gray-200">
								2. {$i18n.t('Authorize ECHO with Google')}
							</div>
							<div>
								<a
									href={flow.auth_url}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex h-7 items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white transition hover:bg-blue-700"
								>
									{$i18n.t('Open Google Authorization')}
								</a>
							</div>
						</div>
					</div>
				{/if}
			</UserSettingSection>

			<UserSettingSection title={$i18n.t('settings.personal.llm_auth.apiKeysSection.title')}>
				<div class="mb-2 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('settings.personal.llm_auth.apiKeysSection.description')}
					<a
						href="https://aistudio.google.com/apikey"
						target="_blank"
						rel="noopener noreferrer"
						class="ml-1 text-blue-600 underline hover:text-blue-700 dark:text-blue-400"
					>
						Google AI Studio
					</a>
				</div>

				<UserSettingField
					forId="llm-auth-primary-key"
					label={$i18n.t('settings.personal.llm_auth.primaryKey.label')}
					description={statusData.api_keys.primary_configured
						? `${$i18n.t('Configured')}: ${statusData.api_keys.primary_masked}`
						: $i18n.t('Not configured')}
				>
					<div class="flex items-center gap-2">
						<input
							id="llm-auth-primary-key"
							type="text"
							style="-webkit-text-security: disc;"
							name="echo-ai-studio-primary-key"
							class={inputClass}
							bind:value={primaryKeyInput}
							placeholder="AIza... / AQ...."
							autocomplete="off"
							spellcheck="false"
							data-lpignore="true"
							data-1p-ignore="true"
							data-form-type="other"
						/>
						<button
							type="button"
							class={primaryBtnClass}
							disabled={savingPrimary || !primaryKeyInput.trim()}
							on:click={() => handleSaveKey('primary')}
						>
							{#if savingPrimary}<Spinner className="size-3.5" />{/if}
							<span>{$i18n.t('Save')}</span>
						</button>
						{#if statusData.api_keys.primary_configured}
							<button
								type="button"
								class={secondaryBtnClass}
								on:click={() => handleDeleteKey('primary')}
							>
								{$i18n.t('Delete')}
							</button>
						{/if}
					</div>
				</UserSettingField>

				<UserSettingField
					forId="llm-auth-secondary-key"
					label={$i18n.t('settings.personal.llm_auth.secondaryKey.label')}
					description={statusData.api_keys.secondary_configured
						? `${$i18n.t('Configured')}: ${statusData.api_keys.secondary_masked}`
						: $i18n.t('Not configured')}
				>
					<div class="flex items-center gap-2">
						<input
							id="llm-auth-secondary-key"
							type="text"
							style="-webkit-text-security: disc;"
							name="echo-ai-studio-secondary-key"
							class={inputClass}
							bind:value={secondaryKeyInput}
							placeholder="AIza... / AQ...."
							autocomplete="off"
							spellcheck="false"
							data-lpignore="true"
							data-1p-ignore="true"
							data-form-type="other"
						/>
						<button
							type="button"
							class={primaryBtnClass}
							disabled={savingSecondary || !secondaryKeyInput.trim()}
							on:click={() => handleSaveKey('secondary')}
						>
							{#if savingSecondary}<Spinner className="size-3.5" />{/if}
							<span>{$i18n.t('Save')}</span>
						</button>
						{#if statusData.api_keys.secondary_configured}
							<button
								type="button"
								class={secondaryBtnClass}
								on:click={() => handleDeleteKey('secondary')}
							>
								{$i18n.t('Delete')}
							</button>
						{/if}
					</div>
				</UserSettingField>
			</UserSettingSection>

			<UserSettingSection title={$i18n.t('settings.personal.llm_auth.dangerSection.title')}>
				<UserSettingRow
					label={$i18n.t('settings.personal.llm_auth.reset.label')}
					description={$i18n.t('settings.personal.llm_auth.reset.description')}
				>
					<button
						type="button"
						class={dangerBtnClass}
						disabled={resetting}
						on:click={() => {
							showResetConfirm = true;
						}}
					>
						{#if resetting}<Spinner className="size-3.5" />{/if}
						<span>{$i18n.t('Reset LLM Authentication')}</span>
					</button>
				</UserSettingRow>
			</UserSettingSection>
		{/if}
	</div>
</div>

<ConfirmDialog
	bind:show={showResetConfirm}
	title={$i18n.t('Reset LLM Authentication?')}
	message={$i18n.t(
		'This will delete your Google OAuth2 tokens and AI Studio API keys from ECHO. You will need to re-authenticate.'
	)}
	confirmLabel={$i18n.t('Reset')}
	on:confirm={handleResetAuth}
/>
