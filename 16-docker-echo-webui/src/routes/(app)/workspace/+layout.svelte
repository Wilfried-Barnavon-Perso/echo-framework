<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import {
		WEBUI_NAME,
		config,
		showSidebar,
		user,
		mobile,
		workspaceActions,
		workspaceCounts
	} from '$lib/stores';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { getPromptItems } from '$lib/apis/prompts';
	import { listEchoSkills, listEchoMetaArtifacts } from '$lib/apis/echo';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';
	import { formatNumber } from '$lib/utils';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let loaded = false;
	let lastPath = '';
	let activeWorkspaceSection = '';
	let visibleActions = [];

	$: if ($page.url.pathname !== lastPath) {
		lastPath = $page.url.pathname;
		workspaceActions.set([]);
	}

	$: if (loaded && $page.url.pathname.startsWith('/workspace')) {
		loadWorkspaceCounts();
	}

	$: activeWorkspaceSection = $page.url.pathname.split('/')[2] ?? '';
	$: visibleActions = $workspaceActions.filter((action) => action.visible ?? true);

	const getCount = (res: any) => res?.total ?? (Array.isArray(res) ? res.length : null);
	const formatCount = (count: number | null) => formatNumber(count ?? 0);

	const loadWorkspaceCounts = async () => {
		const token = localStorage.token || '';
		const [promptRes, skillRes, metaRes] = await Promise.all([
			getPromptItems(token, null, null, null, null, null, 1).catch(() => null),
			listEchoSkills(token).catch(() => null),
			listEchoMetaArtifacts(token).catch(() => null)
		]);

		workspaceCounts.set({
			models: null,
			knowledge: null,
			prompts: getCount(promptRes),
			skills: Array.isArray(skillRes) ? skillRes.length : null,
			metaArtifacts: metaRes?.total ?? (Array.isArray(metaRes?.items) ? metaRes.items.length : null),
			tools: null
		});
	};

	onMount(async () => {
		// [ECHO-V6-HOOK] Redirection des sections obsolètes vers /workspace/skills
		if (
			$page.url.pathname.includes('/workspace/models') ||
			$page.url.pathname.includes('/workspace/knowledge') ||
			$page.url.pathname.includes('/workspace/tools')
		) {
			goto('/workspace/skills', { replaceState: true });
			return;
		}

		loaded = true;
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Workspace')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		id="workspace-layout"
		class="flex flex-1 min-w-0 w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: 'md:max-w-[calc(100%-42px)]'} max-w-full"
	>
		<div class="flex h-full min-w-0 flex-1 flex-col">
			<nav class="px-1.5 pt-1.5 drag-region select-none">
				<div class="flex items-center gap-0.5 md:gap-1">
					{#if $mobile}
						<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
							<Tooltip
								content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								interactive={true}
							>
								<button
									id="sidebar-toggle-button"
									class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition cursor-"
									aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
									on:click={() => {
										showSidebar.set(!$showSidebar);
									}}
								>
									<div class=" self-center p-1.5">
										<Sidebar className="size-4" />
									</div>
								</button>
							</Tooltip>
						</div>
					{/if}

					<div class="flex w-full items-center">
						<div
							class="flex min-w-0 mr-1.5 items-center gap-0.5 md:gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-normal bg-transparent py-1 touch-auto pointer-events-auto"
						>
							<!-- [ECHO-V6-HOOK] 3 Onglets Souverains : Skills, Méta-Artéfacts, Prompts -->
							<a
								draggable="false"
								data-sveltekit-keepfocus
								aria-current={activeWorkspaceSection === 'skills' ? 'page' : null}
								class="min-w-fit px-2 py-1 text-sm inline-flex items-center gap-1.5 {activeWorkspaceSection ===
								'skills'
									? 'text-gray-900 dark:text-gray-100 font-semibold'
									: 'text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200'} transition select-none"
								href="/workspace/skills"
							>
								<span>Skills</span>
								{#if $workspaceCounts?.skills !== null && $workspaceCounts?.skills !== undefined}
									<span class="text-xs opacity-75 font-mono">
										{formatCount($workspaceCounts.skills)}
									</span>
								{/if}
							</a>

							<a
								draggable="false"
								data-sveltekit-keepfocus
								aria-current={activeWorkspaceSection === 'meta-artifacts' ? 'page' : null}
								class="min-w-fit px-2 py-1 text-sm inline-flex items-center gap-1.5 {activeWorkspaceSection ===
								'meta-artifacts'
									? 'text-gray-900 dark:text-gray-100 font-semibold'
									: 'text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200'} transition select-none"
								href="/workspace/meta-artifacts"
							>
								<span>Méta-Artéfacts</span>
								{#if ($workspaceCounts as any)?.metaArtifacts !== null && ($workspaceCounts as any)?.metaArtifacts !== undefined}
									<span class="text-xs opacity-75 font-mono">
										{formatCount(($workspaceCounts as any).metaArtifacts)}
									</span>
								{/if}
							</a>

							<a
								draggable="false"
								data-sveltekit-keepfocus
								aria-current={activeWorkspaceSection === 'prompts' ? 'page' : null}
								class="min-w-fit px-2 py-1 text-sm inline-flex items-center gap-1.5 {activeWorkspaceSection ===
								'prompts'
									? 'text-gray-900 dark:text-gray-100 font-semibold'
									: 'text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200'} transition select-none"
								href="/workspace/prompts"
							>
								<span>Prompts</span>
								{#if $workspaceCounts?.prompts !== null && $workspaceCounts?.prompts !== undefined}
									<span class="text-xs opacity-75 font-mono">
										{formatCount($workspaceCounts.prompts)}
									</span>
								{/if}
							</a>
						</div>

						<div class="ml-auto flex shrink-0 items-center gap-1">
							<SplitCreateButton actions={visibleActions} />
						</div>
					</div>

					<!-- <div class="flex items-center text-xl font-normal">{$i18n.t('Workspace')}</div> -->
				</div>
			</nav>

			<div
				class="pb-1 flex-1 min-w-0 max-h-full overflow-y-auto overflow-x-hidden"
				class:px-3={!['/workspace/models/create', '/workspace/models/edit'].includes(
					$page.url.pathname
				)}
				id="workspace-container"
			>
				<slot />
			</div>
		</div>
	</div>
{/if}

<style>
	:global(html.high-contrast) a[aria-current='page'] {
		text-decoration-line: underline;
		text-decoration-thickness: 2px;
		text-underline-offset: 3px;
	}
</style>
