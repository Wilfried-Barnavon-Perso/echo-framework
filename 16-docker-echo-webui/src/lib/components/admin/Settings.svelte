<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import { getBackendConfig } from '$lib/apis';
	import { config, user } from '$lib/stores';

	import AdminTabIcon from './Settings/AdminTabIcon.svelte';
	import General from './Settings/General.svelte';
	import Interface from './Settings/Interface.svelte';

	const i18n: any = getContext('i18n');

	const VALID_TABS = ['general', 'interface'] as const;
	let selectedTab = 'general';
	let loaded = false;

	$: {
		const pathParts = $page.url.pathname.split('/');
		const tabFromPath = pathParts[pathParts.length - 1];
		if (VALID_TABS.includes(tabFromPath as any)) {
			selectedTab = tabFromPath;
		} else {
			selectedTab = 'general';
			if (loaded) {
				goto('/admin/settings/general', { replaceState: true });
			}
		}
	}

	const adminConfigSaveHandler = async () => {
		toast.success($i18n.t('Settings saved successfully!'));
		await tick();
		await config.set(await getBackendConfig());
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
			return;
		}
		loaded = true;
	});
</script>

{#if loaded}
	<div class="flex flex-col lg:flex-row w-full h-full pb-2">
		<div
			id="admin-settings-tabs-container"
			class="tabs mx-2 px-2 sm:mx-2.5 lg:mx-0 lg:px-2.5 flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-0.5 lg:flex-col lg:flex-none lg:w-50 dark:text-gray-200 text-sm font-normal text-left scrollbar-none"
		>
			<a
				id="general"
				href="/admin/settings/general"
				draggable="false"
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex items-center gap-2 transition select-none {selectedTab ===
				'general'
					? ''
					: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
			>
				<AdminTabIcon id="general" className="size-4" strokeWidth="2" />
				<div class="self-center">{$i18n.t('settings.admin.general.title')}</div>
			</a>

			<a
				id="interface"
				href="/admin/settings/interface"
				draggable="false"
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex items-center gap-2 transition select-none {selectedTab ===
				'interface'
					? ''
					: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
			>
				<AdminTabIcon id="interface" className="size-4" strokeWidth="2" />
				<div class="self-center">{$i18n.t('settings.admin.interface.title')}</div>
			</a>
		</div>

		<div class="flex-1 min-h-0 px-3.5 lg:pr-[1rem] lg:pl-0 overflow-y-auto">
			{#if selectedTab === 'general'}
				<General saveHandler={adminConfigSaveHandler} />
			{:else if selectedTab === 'interface'}
				<Interface
					saveHandler={() => {
						toast.success($i18n.t('Settings saved successfully!'));
					}}
				/>
			{/if}
		</div>
	</div>
{/if}
