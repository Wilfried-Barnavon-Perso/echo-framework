<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { config, user } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { updateUserProfile, getSessionUser } from '$lib/apis/auths';
	import { generateInitialsImage } from '$lib/utils';

	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import UserProfileImage from './Account/UserProfileImage.svelte';
	import UserSettingField from './UserSettingField.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n: any = getContext('i18n');

	export let saveHandler: () => void | Promise<void>;

	let profileImageUrl = '';
	let name = '';
	let gender: 'M' | 'F' | 'X' = 'X';
	let usingDefaultAvatar = false;

	const normalizeGender = (value?: string | null): 'M' | 'F' | 'X' => {
		const norm = String(value ?? '').trim().toUpperCase();
		return norm === 'M' || norm === 'F' || norm === 'X' ? norm : 'X';
	};

	const getEchoAvatarUrl = (value?: string | null): string => {
		const g = normalizeGender(value);
		const file =
			g === 'M'
				? 'user-echo-homme.png'
				: g === 'F'
					? 'user-echo-femme.png'
					: 'user-echo-neutre.png';
		return `${WEBUI_BASE_URL}/static/${file}`;
	};

	const handleGenderChange = () => {
		gender = normalizeGender(gender);
		if (usingDefaultAvatar) {
			profileImageUrl = getEchoAvatarUrl(gender);
		}
	};

	const handleAvatarModeChange = (event: CustomEvent<{ isDefault: boolean }>) => {
		usingDefaultAvatar = Boolean(event.detail?.isDefault);
	};

	const inputClass =
		'h-7 w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const submitHandler = async () => {
		if (name !== $user?.name) {
			if (profileImageUrl === generateInitialsImage($user?.name) || profileImageUrl === '') {
				profileImageUrl = generateInitialsImage(name);
			}
		}

		const updatedUser = await updateUserProfile(localStorage.token, {
			name: name,
			profile_image_url: profileImageUrl,
			bio: $user?.bio ?? null,
			gender: normalizeGender(gender),
			date_of_birth: $user?.date_of_birth ?? null
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (updatedUser) {
			const sessionUser = await getSessionUser(localStorage.token).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (sessionUser) {
				await user.set(sessionUser);
				name = sessionUser?.name ?? name;
				profileImageUrl = sessionUser?.profile_image_url ?? profileImageUrl;
				gender = normalizeGender(sessionUser?.gender);
				usingDefaultAvatar = Boolean(sessionUser?.is_default_avatar);
			}
			return true;
		}
		return false;
	};

	onMount(async () => {
		const sessionUser = await getSessionUser(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (sessionUser) {
			name = sessionUser?.name ?? '';
			profileImageUrl = sessionUser?.profile_image_url ?? '';
			gender = normalizeGender(sessionUser?.gender);
			usingDefaultAvatar = Boolean(sessionUser?.is_default_avatar);
		}
	});
</script>

<form
	id="tab-account"
	class="flex h-full flex-col text-sm"
	on:submit|preventDefault={async () => {
		const res = await submitHandler();

		if (res) {
			saveHandler();
		}
	}}
>
	<div class="flex-1 min-h-0 w-full overflow-y-auto scrollbar-hover pr-1.5">
		<div class="mb-4 flex items-center justify-between gap-2">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">
				{$i18n.t('settings.personal.account.title')}
			</h2>
			<span
				class="shrink-0 rounded-lg bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600 dark:bg-gray-850 dark:text-gray-300"
			>
				{$config?.features?.auth_trusted_header ? 'Authentification gérée par le portail SSO' : 'Compte local'}
			</span>
		</div>

		<UserSettingSection title={$i18n.t('settings.personal.account.sections.profile.title')} first>
			<UserProfileImage
				bind:profileImageUrl
				user={$user}
				variant="account"
				displayName={$user?.name}
				defaultProfileImageUrl={getEchoAvatarUrl(gender)}
				on:avatarModeChange={handleAvatarModeChange}
			/>

			<UserSettingField
				label={$i18n.t('settings.personal.account.name.label')}
				description={$i18n.t('settings.personal.account.name.description')}
			>
				<input
					class={inputClass}
					type="text"
					bind:value={name}
					aria-label={$i18n.t('settings.personal.account.name.label')}
					required
					placeholder={$i18n.t('Enter your name')}
				/>
			</UserSettingField>

			<UserSettingField
				label="Genre grammatical"
				description="Définit l'accord dans ECHO et l'icône par défaut si aucun avatar personnalisé n'est défini."
			>
				<SettingsSelect
					bind:value={gender}
					ariaLabel="Genre grammatical"
					on:change={handleGenderChange}
				>
					<option value="X">Neutre / Non précisé</option>
					<option value="M">Homme</option>
					<option value="F">Femme</option>
				</SettingsSelect>
			</UserSettingField>

			<UserSettingRow label={$config?.features?.auth_trusted_header ? 'E-mail SSO' : 'E-mail'}>
				<span class="font-mono text-xs text-gray-700 dark:text-gray-200">
					{$user?.email || '—'}
				</span>
			</UserSettingRow>

			<UserSettingRow label="Rôle">
				<span
					class="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium capitalize text-gray-700 dark:bg-gray-800 dark:text-gray-200"
				>
					{$user?.role || 'user'}
				</span>
			</UserSettingRow>
		</UserSettingSection>
	</div>

	<div class="shrink-0 flex w-full justify-end pt-3 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
