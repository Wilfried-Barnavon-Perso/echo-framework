<script>
	import { getContext, onMount } from 'svelte';

	import { goto } from '$app/navigation';
	import { adminUserCount, config, user } from '$lib/stores';
	import { page } from '$app/stores';
	import { getUsers } from '$lib/apis/users';
	import { formatNumber } from '$lib/utils';

	import UserList from './Users/UserList.svelte';

	const i18n = getContext('i18n');

	let selectedTab = 'overview';
	$: {
		const pathParts = $page.url.pathname.split('/');
		const tabFromPath = pathParts[pathParts.length - 1];
		if (tabFromPath === 'groups') {
			goto('/admin/users/overview', { replaceState: true });
		}
		selectedTab = 'overview';
	}

	$: if (selectedTab) {
		// scroll to selectedTab
		scrollToTab(selectedTab);
	}

	$: if (loaded && selectedTab) {
		loadCounts();
	}

	const scrollToTab = (tabId) => {
		const tabElement = document.getElementById(tabId);
		if (tabElement) {
			tabElement.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
		}
	};

	let loaded = false;
	$: usersSeatLimit = $config?.license_metadata?.seats ?? null;
	$: usersCountExceeded = usersSeatLimit !== null && ($adminUserCount ?? 0) > usersSeatLimit;
	$: formattedUserCount =
		$adminUserCount === null
			? null
			: usersSeatLimit !== null
				? `${formatNumber($adminUserCount)} of ${formatNumber(usersSeatLimit)}`
				: formatNumber($adminUserCount);
	const loadCounts = async () => {
		const usersRes = await getUsers(localStorage.token, undefined, 'created_at', 'asc', 1).catch(
			() => null
		);

		adminUserCount.set(usersRes?.total ?? null);
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
		}

		loaded = true;

		const containerElement = document.getElementById('users-tabs-container');

		if (containerElement) {
			containerElement.addEventListener('wheel', function (event) {
				if (event.deltaY !== 0) {
					// Adjust horizontal scroll position based on vertical scroll
					containerElement.scrollLeft += event.deltaY;
				}
			});
		}

		// Scroll to the selected tab on mount
		scrollToTab(selectedTab);
	});
</script>

{#if loaded}
	<div class="flex flex-col lg:flex-row w-full h-full pb-2">
		<div
			id="users-tabs-container"
			class="tabs mx-2 px-2 sm:mx-2.5 lg:mx-0 lg:px-2.5 flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-0 lg:flex-col lg:flex-none lg:w-50 dark:text-gray-200 text-sm font-normal text-left scrollbar-none"
		>
			<a
				id="overview"
				href="/admin/users/overview"
				draggable="false"
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex items-center gap-1.5 text-right transition select-none {selectedTab ===
				'overview'
					? ''
					: ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
			>
				<div class=" self-center">{$i18n.t('Overview')}</div>
				{#if formattedUserCount !== null}
					<div
						class="self-center text-sm {usersCountExceeded
							? `text-red-500 ${selectedTab === 'overview' ? '' : 'opacity-50'}`
							: 'opacity-60'}"
					>
						{formattedUserCount}
					</div>
				{/if}
			</a>
		</div>

		<div class="flex-1 px-3.5 lg:pr-[1rem] lg:pl-0 overflow-y-scroll">
			<UserList />
		</div>
	</div>
{/if}
