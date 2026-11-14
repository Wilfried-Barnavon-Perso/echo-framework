<script lang="ts">
	import { resolveLocalizedResource } from '$lib/utils/localizedContent';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { toast } from 'svelte-sonner';

	dayjs.extend(relativeTime);

	import { WEBUI_NAME, config, user } from '$lib/stores';
	import { onMount, getContext, tick, onDestroy } from 'svelte';

	import { goto } from '$app/navigation';
	import { getFunctionList } from '$lib/apis/functions';

	import Tooltip from '../common/Tooltip.svelte';
	import ProfilePreview from '$lib/components/channel/Messages/Message/ProfilePreview.svelte';
	import ProfileImage from '$lib/components/chat/Messages/ProfileImage.svelte';
	import Search from '../icons/Search.svelte';
	import XMark from '../icons/XMark.svelte';
	import ViewSelector from '../workspace/common/ViewSelector.svelte';
	import TagSelector from '../workspace/common/TagSelector.svelte';
	import CommunityDiscover from '../workspace/common/CommunityDiscover.svelte';
	import { capitalizeFirstLetter } from '$lib/utils';
	import Spinner from '../common/Spinner.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import ChevronUp from '../icons/ChevronUp.svelte';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');

	let tagsContainerElement: HTMLDivElement;
	let viewOption = '';
	let sortKey = 'updated_at';
	let sortDirection = 'desc';

	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;
	let selectedType = '';

	let loaded = false;
	let functions = null;
	let filteredItems = [];

	const handleSearchInput = () => {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			setFilteredItems();
		}, 300);
	};

	$: if (
		functions &&
		selectedType !== undefined &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		sortDirection !== undefined
	) {
		setFilteredItems();
	}

	const setFilteredItems = () => {
		filteredItems = (functions ?? [])
			.filter(
				(f) =>
					(selectedType !== '' ? f.type === selectedType : true) &&
					(query === '' ||
						f.name.toLowerCase().includes(query.toLowerCase()) ||
						resolveLocalizedResource(f, $i18n.language)
							.toLowerCase()
							.includes(query.toLowerCase()) ||
						f.id.toLowerCase().includes(query.toLowerCase()) ||
						(f.user?.name || '').toLowerCase().includes(query.toLowerCase()) ||
						(f.user?.email || '').toLowerCase().includes(query.toLowerCase()) ||
						(f.user?.username || '').toLowerCase().includes(query.toLowerCase())) &&
					(viewOption === '' ||
						(viewOption === 'created' && f.user_id === $user?.id) ||
						(viewOption === 'shared' && f.user_id !== $user?.id))
			)
			.sort((a, b) => {
				const direction = sortDirection === 'asc' ? 1 : -1;

				if (sortKey === 'name') {
					return direction * (a.name ?? '').localeCompare(b.name ?? '');
				}

				return direction * ((a.updated_at ?? 0) - (b.updated_at ?? 0));
			});
	};

	const setSortKey = (key: string) => {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortDirection = key === 'updated_at' ? 'desc' : 'asc';
		}
	};

	const openFunction = (func) => {
		goto(`/admin/functions/edit?id=${encodeURIComponent(func.id)}`);
	};

	const shouldIgnoreRowClick = (target: EventTarget | null) => {
		return target instanceof Element && !!target.closest('button, a, input, [role="menu"]');
	};

	onMount(async () => {
		viewOption = localStorage?.workspaceViewOption || '';
		functions = await getFunctionList(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});

		await tick();
		loaded = true;
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Functions')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div class="px-2.5 w-full">
		<div class="flex flex-col gap-1 px-1 mt-0.5">
			<div class="flex justify-between items-center mb-1 w-full">
				<div class="flex justify-between items-center w-full">
					<div class="flex items-center md:self-center text-sm font-normal px-0.5 gap-1.5 shrink-0">
						<div>
							{$i18n.t('Functions')}
						</div>

						<div class="text-sm font-normal text-gray-500 dark:text-gray-500 opacity-60">
							{filteredItems.length}
						</div>
					</div>

					<div class="flex w-full justify-end">
						<span
							class="shrink-0 rounded-lg bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600 dark:bg-gray-850 dark:text-gray-300"
						>
							Lecture seule — Synchronisé depuis /opt/ECHO
						</span>
					</div>
				</div>
			</div>
		</div>

		<div class="space-y-1">
			<div class="flex h-8 flex-1 items-center w-full gap-2">
				<div class="flex min-w-0 flex-1">
					<div class=" self-center ml-1 mr-3">
						<Search className="size-3.5" />
					</div>
					<input
						class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={query}
						on:input={handleSearchInput}
						placeholder={$i18n.t('Search Functions')}
						aria-label={$i18n.t('Search Functions')}
					/>

					{#if query}
						<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
							<button
								class="py-0.5 rounded-full transition hover:text-gray-900 dark:hover:text-gray-100 text-gray-500 dark:text-gray-400 px-0"
								aria-label={$i18n.t('Clear search')}
								on:click={() => {
									query = '';
									handleSearchInput();
								}}
							>
								<XMark className="size-3" strokeWidth="2" />
							</button>
						</div>
					{/if}
				</div>

				<div
					class="flex max-w-[55%] shrink-0 overflow-x-auto scrollbar-none"
					bind:this={tagsContainerElement}
					on:wheel={(e) => {
						if (e.deltaY !== 0) {
							e.preventDefault();
							e.currentTarget.scrollLeft += e.deltaY;
						}
					}}
				>
					<div
						class="flex w-fit gap-0.5 text-center text-sm rounded-full bg-transparent whitespace-nowrap"
					>
						<ViewSelector
							bind:value={viewOption}
							align="end"
							onChange={async (value) => {
								localStorage.workspaceViewOption = value;

								await tick();
							}}
						/>

						<TagSelector
							bind:value={selectedType}
							align="end"
							items={[
								{ value: 'pipe', label: $i18n.t('Pipe') },
								{ value: 'filter', label: $i18n.t('Filter') },
								{ value: 'action', label: $i18n.t('Action') },
								{ value: 'event', label: $i18n.t('Event') }
							]}
						/>
					</div>
				</div>
			</div>

			{#if (filteredItems ?? []).length !== 0}
				<div class="my-1">
					<div
						class="flex w-full items-center gap-2 px-1.5 pb-0.5 text-xs text-gray-400 dark:text-gray-600"
					>
						<button
							class="flex min-w-0 flex-1 items-center gap-1 py-0.5 text-left"
							type="button"
							on:click={() => setSortKey('name')}
						>
							{$i18n.t('Title')}
							{#if sortKey === 'name'}
								{#if sortDirection === 'asc'}
									<ChevronUp className="size-2" />
								{:else}
									<ChevronDown className="size-2" />
								{/if}
							{/if}
						</button>

						<div class="hidden w-44 shrink-0 md:block"></div>

						<button
							class="flex w-36 shrink-0 items-center justify-end gap-1 py-0.5 text-right"
							type="button"
							on:click={() => setSortKey('updated_at')}
						>
							{$i18n.t('Updated at')}
							{#if sortKey === 'updated_at'}
								{#if sortDirection === 'asc'}
									<ChevronUp className="size-2" />
								{:else}
									<ChevronDown className="size-2" />
								{/if}
							{/if}
						</button>
					</div>

					<div class="grid gap-y-0.5">
						{#each filteredItems as func (func.id)}
							<div
								class="group flex min-h-8 w-full cursor-pointer items-center gap-2 overflow-hidden rounded-xl px-2 py-1 text-left"
								role="presentation"
								on:click={(e) => {
									if (shouldIgnoreRowClick(e.target)) return;
									openFunction(func);
								}}
							>
								<div class="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
									<div class="flex min-w-0 flex-1 flex-col overflow-hidden">
										<div class="flex min-w-0 items-center gap-2 overflow-hidden">
											<div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
												<div
													class="shrink-0 rounded-sm bg-gray-500/20 px-1 text-[0.625rem] uppercase leading-4 text-gray-700 dark:text-gray-200"
												>
													{func.type}
												</div>

												<Tooltip content={func.id} className="min-w-0" placement="top-start">
													<a
														href={`/admin/functions/edit?id=${encodeURIComponent(func.id)}`}
														class="focus-ring block truncate rounded text-[0.8125rem] leading-5 text-gray-800 group-hover:underline focus-visible:underline dark:text-gray-200"
														>{resolveLocalizedResource(func, $i18n.language)}</a
													>
												</Tooltip>

												{#if func?.meta?.manifest?.version}
													<div
														class="min-w-0 max-w-[40%] shrink-0 truncate text-[0.6875rem] leading-5 text-gray-500"
													>
														v{func?.meta?.manifest?.version ?? ''}
													</div>
												{/if}

												<Tooltip
													content={dayjs(func.updated_at * 1000)
														.locale($i18n.language)
														.format('LLLL')}
												>
													<div
														class="shrink-0 truncate text-[0.6875rem] leading-5 text-gray-400 dark:text-gray-600"
													>
														{dayjs(func.updated_at * 1000)
															.locale($i18n.language)
															.fromNow()}
													</div>
												</Tooltip>
											</div>
										</div>

										{#if resolveLocalizedResource(func, $i18n.language, 'description')}
											<Tooltip
												content={resolveLocalizedResource(func, $i18n.language, 'description')}
												className="min-w-0"
												placement="top-start"
											>
												<div
													class="mt-0.5 truncate text-[0.6875rem] leading-4 text-gray-400 dark:text-gray-600"
												>
													{resolveLocalizedResource(func, $i18n.language, 'description')}
												</div>
											</Tooltip>
										{/if}
									</div>
								</div>

								<div
									class="hidden max-w-44 shrink-0 self-center truncate text-right text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-500 md:block"
								>
									<ProfilePreview user={func?.user ?? null} side="left">
										<span class="flex max-w-44 items-center gap-1.75">
											<ProfileImage
												src={func?.user?.id
													? `/api/v1/users/${func.user.id}/profile/image`
													: '/user.png'}
												className="size-4 shrink-0 rounded-full!"
											/>
											<span class="min-w-0 max-w-24 truncate">
												{capitalizeFirstLetter(
													func?.user?.name ?? func?.user?.email ?? $i18n.t('Deleted User')
												)}
											</span>
										</span>
									</ProfilePreview>
								</div>

								<div class="ml-2 flex shrink-0 flex-row items-center gap-1.5 self-center">
									{#if func.is_global}
										<span
											class="rounded-full bg-blue-500/10 px-2 py-0.5 text-[0.6875rem] font-medium text-blue-600 dark:text-blue-400"
										>
											Globale
										</span>
									{/if}
									<span
										class="rounded-full px-2 py-0.5 text-[0.6875rem] font-medium {func.is_active
											? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
											: 'bg-gray-500/10 text-gray-500 dark:text-gray-400'}"
									>
										{func.is_active ? 'Activée' : 'Désactivée'}
									</span>
								</div>
							</div>
						{/each}
					</div>
				</div>
			{:else}
				<div class="flex w-full flex-col items-center justify-center py-16 pb-24">
					<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
						<div class="mb-1.5 text-sm">{$i18n.t('No functions found')}</div>
						<div class="text-center text-xs leading-5 text-gray-500">
							{$i18n.t('Try adjusting your search or filter to find what you are looking for.')}
						</div>
					</div>
				</div>
			{/if}
		</div>

		<!-- <div class=" text-gray-500 text-xs mt-1 mb-2">
	ⓘ {$i18n.t(
		'Admins have access to all tools at all times; users need tools assigned per model in the workspace.'
	)}
</div> -->

		{#if $config?.features.enable_community_sharing}
			<CommunityDiscover
				href="https://openwebui.com/search?type=function"
				title={$i18n.t('Discover a function')}
				description={$i18n.t('Discover, download, and explore custom functions')}
			/>
		{/if}
	</div>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}
