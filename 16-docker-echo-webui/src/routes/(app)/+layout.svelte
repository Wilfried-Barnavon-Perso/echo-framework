<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, onDestroy, tick, getContext } from 'svelte';

import {
	requestEchoTabOpen,
	startEchoWmStream,
	stopEchoWmStream,
	startEchoEdgeEmbedding,
	stopEchoEdgeEmbedding,
	type EchoTabKind
} from '$lib/stores/echo';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { getBackendConfig, getModels, getToolServersData } from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { getSessionUser } from '$lib/apis/auths';
	import { getBanners } from '$lib/apis/configs';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { getUserSettings } from '$lib/apis/users';
	import { setAppFontFamily, setTextScale } from '$lib/utils/text-scale';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import {
		config,
		user,
		settings,
		models,
		knowledge,
		tools,
		skills,
		socket,
		functions,
		tags,
		banners,
		showSettings,
		showChangelog,
		temporaryChatEnabled,
		toolServers,
		terminalServers,
		selectedTerminalId,
		showSearch,
		showSidebar,
		showTabs,
		mobile,
		chatId,
		chats
	} from '$lib/stores';

	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import SettingsModal from '$lib/components/chat/SettingsModal.svelte';
	import ChangelogModal from '$lib/components/ChangelogModal.svelte';
	import AccountPending from '$lib/components/layout/Overlay/AccountPending.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { loadKeybindings, matchKeybinding, Shortcut } from '$lib/shortcuts';
	import { canUseTemporaryChats } from '$lib/utils/settings-access';

	const i18n = getContext('i18n');

	let loaded = false;

	let handledSettingsUrl = '';

	const clearChatInputStorage = () => {
		const chatInputKeys = Object.keys(localStorage).filter((key) => key.startsWith('chat-input'));
		if (chatInputKeys.length > 0) {
			chatInputKeys.forEach((key) => {
				localStorage.removeItem(key);
			});
		}
	};

	const setUserSettings = async (cb?: () => Promise<void>) => {
		const userSettings = await getUserSettings(localStorage.token);

		if (userSettings?.ui) {
			settings.set(userSettings.ui);
		}
		loadKeybindings(userSettings?.keybindings);

		setTextScale($settings?.textScale ?? 1);
		setAppFontFamily($settings?.fontFamily ?? null);

		if (cb) {
			await cb();
		}
	};

	const setModels = async () => {
		models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections ? ($settings?.directConnections ?? null) : null
			)
		);
	};

	const setToolServers = async () => {
		if (!$config?.features?.enable_tool_servers) {
			toolServers.set([]);
			terminalServers.set([]);
			return;
		}
		let toolServersData = await getToolServersData(
			$config?.features?.enable_direct_integrations ? ($settings?.toolServers ?? []) : []
		);
		toolServersData = toolServersData.filter((data) => {
			if (!data || data.error) {
				toast.error(
					$i18n.t(`Failed to connect to {{URL}} OpenAPI tool server`, {
						URL: data?.url
					})
				);
				return false;
			}
			return true;
		});
		toolServers.set(toolServersData);

		// Inject enabled terminal servers as always-on tool servers
		const enabledTerminals = (
			$config?.features?.enable_direct_integrations
				? (($settings as any)?.terminalServers ?? [])
				: []
		).filter((s: any) => s.enabled || s.url === $selectedTerminalId);

		// Fetch terminal servers the user has access to (for FileNav + terminal_id)
		const systemTerminals = await getTerminalServers(localStorage.token);
		const allTerminals = [
			...(enabledTerminals.length > 0
				? (
						await getToolServersData(
							enabledTerminals.map((t: any) => ({
								url: t.url,
								auth_type: t.auth_type ?? 'bearer',
								key: t.key ?? '',
								path: t.path ?? '/openapi.json',
								config: { enable: true }
							}))
						)
					)
						.filter((data) => {
							if (!data || data.error) {
								toast.error(
									$i18n.t(`Failed to connect to {{URL}} terminal server`, {
										URL: data?.url
									})
								);
								return false;
							}
							return true;
						})
						.map((data, i) => ({
							...data,
							key: enabledTerminals[i]?.key ?? '',
							config: enabledTerminals[i]?.config ?? data?.config ?? {}
						}))
				: []),
			// Store with proxy URL and session key for FileNav file browsing
			...systemTerminals.map((t) => ({
				id: t.id,
				url: `${WEBUI_API_BASE_URL}/terminals/${t.id}`,
				name: t.name,
				key: localStorage.token,
				contexts: t.contexts ?? {},
				config: t.config ?? {}
			}))
		];
		terminalServers.set(allTerminals);
		if (allTerminals.length > 0 && !$selectedTerminalId) {
			const defaultServer = allTerminals.find((t) => t.id === 'echo-coding-worker') ?? allTerminals[0];
			selectedTerminalId.set(defaultServer.id);
			localStorage.selectedTerminalId = defaultServer.id;
		}
	};

	const setBanners = async () => {
		const bannersData = await getBanners(localStorage.token);
		banners.set(bannersData);
	};

	const setTools = async () => {
		const toolsData = await getTools(localStorage.token);
		tools.set(toolsData);
	};

	const refreshAccess = async () => {
		if (!localStorage.token) return;
		try {
			user.set(await getSessionUser(localStorage.token));
			const results = await Promise.allSettled([
				getBackendConfig().then((value) => config.set(value)),
				setModels(),
				setTools(),
				setToolServers(),
				getSkills(localStorage.token).then((value) => skills.set(value))
			]);
			for (const result of results) {
				if (result.status === 'rejected') console.error('Unable to refresh access', result.reason);
			}
		} catch (error) {
			console.error('Unable to refresh permissions', error);
		}
	};

	onMount(() => {
		const socketInstance = $socket;
		socketInstance?.on('access:updated', refreshAccess);
		return () => {
			socketInstance?.off('access:updated', refreshAccess);
		};
	});

	const openSettingsFromUrl = async () => {
		const requestedSettings = $page.url.searchParams.get('settings');
		if (!requestedSettings) {
			// Param handled and stripped; allow the same deep link to be
			// handled again later in this session.
			handledSettingsUrl = '';
			return;
		}

		const urlKey = `${$page.url.pathname}${$page.url.search}${$page.url.hash}`;
		if (handledSettingsUrl === urlKey) {
			return;
		}
		handledSettingsUrl = urlKey;

		showSettings.set(
			requestedSettings.startsWith('admin:') && $user?.role !== 'admin'
				? 'general'
				: requestedSettings
		);

		const params = new URLSearchParams($page.url.searchParams);
		params.delete('settings');
		const query = params.toString();
		await goto(`${$page.url.pathname}${query ? `?${query}` : ''}${$page.url.hash}`, {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	};

	const gotoAuth = async () => {
		const currentUrl = `${$page.url.pathname}${$page.url.search}`;
		await goto(`/auth?redirect=${encodeURIComponent(currentUrl)}`);
	};

	const navigateChat = async (direction: -1 | 1) => {
		if (!$chats?.length) return;

		const currentIndex = $chats.findIndex((chat) => chat.id === $chatId);
		const nextChat = currentIndex === -1 ? $chats[0] : $chats[currentIndex + direction];

		if (nextChat) {
			await goto(`/c/${nextChat.id}`);
		}
	};

	onMount(async () => {
		if ($user === undefined || $user === null) {
			await gotoAuth();
			return;
		}
		if (!['user', 'admin'].includes($user?.role)) {
			return;
		}

		clearChatInputStorage();
		try {
			await Promise.all([
				setBanners().catch((e) => console.error('Failed to load banners:', e)),
				setTools().catch((e) => console.error('Failed to load tools:', e)),
				setUserSettings(async () => {
					await setModels().catch((e) => console.error('Failed to load models:', e));
				})
			]);
		} catch (e) {
			console.error('Failed to load user settings:', e);
			toast.error($i18n.t('Failed to load Interface settings'));
			return;
		}

		selectedTerminalId.set(
			$config?.features?.enable_tool_servers ? (localStorage.selectedTerminalId ?? null) : null
		);

		setToolServers().catch((e) => {
			console.error('Failed to load tool servers:', e);
			terminalServers.set([]);
		});

		const setupKeyboardShortcuts = () => {
			const handleKeydown = async (event: KeyboardEvent) => {
				if ($settings?.keyboardShortcuts === false) {
					return;
				}

				const shortcut = matchKeybinding(event);
				if (shortcut === Shortcut.SEARCH) {
					console.log('Shortcut triggered: SEARCH');
					event.preventDefault();
					showSearch.set(!$showSearch);
				} else if (shortcut === Shortcut.NEW_CHAT) {
					console.log('Shortcut triggered: NEW_CHAT');
					event.preventDefault();
					document.getElementById('sidebar-new-chat-button')?.click();
				} else if (shortcut === Shortcut.FOCUS_INPUT) {
					console.log('Shortcut triggered: FOCUS_INPUT');
					event.preventDefault();
					document.getElementById('chat-input')?.focus();
				} else if (shortcut === Shortcut.COPY_LAST_CODE_BLOCK) {
					console.log('Shortcut triggered: COPY_LAST_CODE_BLOCK');
					event.preventDefault();
					[...document.getElementsByClassName('copy-code-button')]?.at(-1)?.click();
				} else if (shortcut === Shortcut.COPY_LAST_RESPONSE) {
					console.log('Shortcut triggered: COPY_LAST_RESPONSE');
					event.preventDefault();
					[...document.getElementsByClassName('copy-response-button')]?.at(-1)?.click();
				} else if (shortcut === Shortcut.TOGGLE_SIDEBAR) {
					console.log('Shortcut triggered: TOGGLE_SIDEBAR');
					event.preventDefault();
					showSidebar.set(!$showSidebar);
				} else if (shortcut === Shortcut.NAVIGATE_CHAT_UP) {
					console.log('Shortcut triggered: NAVIGATE_CHAT_UP');
					event.preventDefault();
					await navigateChat(-1);
				} else if (shortcut === Shortcut.NAVIGATE_CHAT_DOWN) {
					console.log('Shortcut triggered: NAVIGATE_CHAT_DOWN');
					event.preventDefault();
					await navigateChat(1);
				} else if (shortcut === Shortcut.TOGGLE_CONTROLS) {
					console.log('Shortcut triggered: TOGGLE_CONTROLS');
					event.preventDefault();
					showTabs.set(!$showTabs);
				} else if (shortcut === Shortcut.DELETE_CHAT) {
					console.log('Shortcut triggered: DELETE_CHAT');
					event.preventDefault();
					document.getElementById('delete-chat-button')?.click();
				} else if (shortcut === Shortcut.OPEN_SETTINGS) {
					console.log('Shortcut triggered: OPEN_SETTINGS');
					event.preventDefault();
					showSettings.set(!$showSettings);
				} else if (shortcut === Shortcut.SHOW_SHORTCUTS) {
					console.log('Shortcut triggered: SHOW_SHORTCUTS');
					event.preventDefault();
					showSettings.set('shortcuts');
				} else if (shortcut === Shortcut.OPEN_MODEL_SELECTOR) {
					console.log('Shortcut triggered: OPEN_MODEL_SELECTOR');
					event.preventDefault();
					document.getElementById('model-selector-model-button')?.click();
				} else if (shortcut === Shortcut.NEW_TEMPORARY_CHAT) {
					console.log('Shortcut triggered: NEW_TEMPORARY_CHAT');
					event.preventDefault();
					if (!canUseTemporaryChats({ user: $user, config: null })) {
						return;
					}
					if ($user?.role !== 'admin' && $user?.permissions?.chat?.temporary_enforced) {
						temporaryChatEnabled.set(true);
					} else {
						temporaryChatEnabled.set(!$temporaryChatEnabled);
					}
					await goto('/');
					setTimeout(() => {
						document.getElementById('new-chat-button')?.click();
					}, 0);
				} else if (shortcut === Shortcut.GENERATE_MESSAGE_PAIR) {
					console.log('Shortcut triggered: GENERATE_MESSAGE_PAIR');
					event.preventDefault();
					document.getElementById('generate-message-pair-button')?.click();
				} else if (shortcut === Shortcut.ALLOW_TOOL_CALL) {
					const button = [...document.getElementsByClassName('tool-call-allow-button')]
						.reverse()
						.find((el) => !(el as HTMLButtonElement).disabled) as HTMLButtonElement | undefined;
					if (button) {
						console.log('Shortcut triggered: ALLOW_TOOL_CALL');
						event.preventDefault();
						button.click();
					}
				} else if (shortcut === Shortcut.DENY_TOOL_CALL) {
					const button = [...document.getElementsByClassName('tool-call-deny-button')]
						.reverse()
						.find((el) => !(el as HTMLButtonElement).disabled) as HTMLButtonElement | undefined;
					if (button) {
						console.log('Shortcut triggered: DENY_TOOL_CALL');
						event.preventDefault();
						button.click();
					}
				} else if (
					shortcut === Shortcut.REGENERATE_RESPONSE &&
					document.activeElement?.id === 'chat-input'
				) {
					console.log('Shortcut triggered: REGENERATE_RESPONSE');
					event.preventDefault();
					[...document.getElementsByClassName('regenerate-response-button')]?.at(-1)?.click();
				}
			};
			document.addEventListener('keydown', handleKeydown);
			cleanupKeydown = () => document.removeEventListener('keydown', handleKeydown);
		};
		setupKeyboardShortcuts();

		if ($user?.role === 'admin' && ($settings?.showChangelog ?? true)) {
			showChangelog.set($settings?.version !== $config.version);
		}

		if ($user?.role === 'admin' || ($user?.permissions?.chat?.temporary ?? true)) {
			if ($page.url.searchParams.get('temporary-chat') === 'true') {
				temporaryChatEnabled.set(true);
			}

			if ($user?.role !== 'admin' && $user?.permissions?.chat?.temporary_enforced) {
				temporaryChatEnabled.set(true);
			}
		}

		// Persist showTabs: track open/close state separately from saved size
		// chatControlsSize always retains the last width for openPane()
		await showTabs.set(!$mobile ? localStorage.showTabs === 'true' : false);
		showTabs.subscribe((value) => {
			localStorage.showTabs = value ? 'true' : 'false';
		});

		// Persist selectedTerminalId across page loads
		selectedTerminalId.subscribe((value) => {
			if (!$config?.features?.enable_tool_servers) return;
			if (value === null) {
				delete localStorage.selectedTerminalId;
			} else {
				localStorage.selectedTerminalId = value;
			}
		});

		await tick();

		// [ECHO-V6-HOOK] Keep-Alive TCP 120s pour proxys & WAF
		keepAliveInterval = setInterval(() => {
			fetch('/health', { method: 'HEAD', cache: 'no-store', keepalive: true }).catch(() => {});
		}, 120000);

		const handleEchoOpenTab = (event: Event) => {
			const detail = (event as CustomEvent<{ chatId?: string; kind?: EchoTabKind; payload?: Record<string, any> }>)?.detail;
			if (!detail?.kind) return;
			const cid = detail.chatId || $chatId || '';
			if (!cid) return;
			requestEchoTabOpen(cid, detail.kind, detail.payload);
		};
		window.addEventListener('echo:open-tab', handleEchoOpenTab);
		cleanupEchoOpenTab = () => window.removeEventListener('echo:open-tab', handleEchoOpenTab);

		// [ECHO-V6-HOOK] Pont natif ancres #codex: -> onglet echo-codex
		const CODEX_ANCHOR_PREFIX = '#codex:';
		const CODEX_DEFAULT_WORKSPACE = 'main';
		(window as any).echoOpenCodex = (
			targetChatId?: string,
			targetWorkspace?: string,
			targetFile?: string
		) => {
			const urlMatch = window.location.pathname.match(/\/c\/([a-zA-Z0-9_-]+)/);
			const currentChatId = (urlMatch ? urlMatch[1] : '') || $chatId || '';
			const ws = targetWorkspace || CODEX_DEFAULT_WORKSPACE;
			const effectiveCid = targetChatId || currentChatId;
			const hostChatId = currentChatId || effectiveCid;
			if (!hostChatId) return;

			window.dispatchEvent(
				new CustomEvent('echo:open-tab', {
					detail: {
						chatId: hostChatId,
						kind: 'echo-codex',
						payload: {
							targetChatId: effectiveCid,
							workspace: ws,
							file: targetFile || ''
						}
					}
				})
			);
		};

		const handleCodexAnchorClick = (e: MouseEvent) => {
			const targetEl = e.target as Element | null;
			const link = targetEl?.closest ? (targetEl.closest(`a[href*="${CODEX_ANCHOR_PREFIX}"]`) as HTMLAnchorElement | null) : null;
			if (!link) return;

			link.removeAttribute('target');
			link.setAttribute('target', '_self');
			e.preventDefault();
			e.stopPropagation();

			const href = link.getAttribute('href') || link.href || '';
			const codexIdx = href.indexOf(CODEX_ANCHOR_PREFIX);
			if (codexIdx === -1) return;

			const raw = href.substring(codexIdx + CODEX_ANCHOR_PREFIX.length);
			const parts = raw.split(':');
			if (parts.length < 3) return;

			const targetChatId = parts[0];
			const targetWorkspace = parts[1];
			let targetFile = parts.slice(2).join(':');
			try {
				targetFile = decodeURIComponent(targetFile);
			} catch {
				// Conserver la chaîne brute si mal encodée
			}

			(window as any).echoOpenCodex(targetChatId, targetWorkspace, targetFile);
		};
		document.addEventListener('click', handleCodexAnchorClick, true);
		cleanupCodexAnchor = () => document.removeEventListener('click', handleCodexAnchorClick, true);

		// [ECHO-V6-HOOK] Flux SSE Window Manager centralisé (jauge contextuelle, onglets, navigateur, Canvas)
		startEchoWmStream(() => $chatId);
		startEchoEdgeEmbedding(() => $mobile);

		loaded = true;
	});

	let keepAliveInterval: any = null;
	let cleanupEchoOpenTab: (() => void) | null = null;
	let cleanupCodexAnchor: (() => void) | null = null;
	let cleanupKeydown: (() => void) | null = null;

	onDestroy(() => {
		if (cleanupKeydown) {
			cleanupKeydown();
			cleanupKeydown = null;
		}
		if (cleanupCodexAnchor) {
			cleanupCodexAnchor();
			cleanupCodexAnchor = null;
		}
		if (cleanupEchoOpenTab) {
			cleanupEchoOpenTab();
			cleanupEchoOpenTab = null;
		}
		if (keepAliveInterval) {
			clearInterval(keepAliveInterval);
			keepAliveInterval = null;
		}
		stopEchoWmStream();
		stopEchoEdgeEmbedding();
	});

	// `$page.url` must be referenced here: `$:` only tracks variables used in
	// the statement itself, and reads inside openSettingsFromUrl don't count —
	// without it, client-side navigations to `?settings=...` are never handled.
	$: if (loaded && $page.url) {
		void openSettingsFromUrl();
	}

	$: if (loaded && ($user === undefined || $user === null)) {
		void gotoAuth();
	}
</script>

<SettingsModal bind:show={$showSettings} />
<ChangelogModal bind:show={$showChangelog} />

{#if $user}
	<div class="app relative">
		<div
			class=" text-gray-700 dark:text-gray-100 bg-white dark:bg-gray-900 h-screen max-h-[100dvh] overflow-auto flex flex-row justify-end"
		>
			{#if !['user', 'admin'].includes($user?.role)}
				<AccountPending />
			{:else}
				<Sidebar />

				{#if loaded}
					<main class="contents">
						<div id="main-content" tabindex="-1" class="sr-only left-0 top-0"></div>
						<slot />
					</main>
				{:else}
					<div
						class="w-full flex-1 h-full flex items-center justify-center {$showSidebar
							? '  md:max-w-[calc(100%-var(--sidebar-width))]'
							: ' '}"
					>
						<Spinner className="size-5" />
					</div>
				{/if}
			{/if}
		</div>
	</div>
{/if}

<style>
	.loading {
		display: inline-block;
		clip-path: inset(0 1ch 0 0);
		animation: l 1s steps(3) infinite;
		letter-spacing: -0.5px;
	}

	@keyframes l {
		to {
			clip-path: inset(0 -1ch 0 0);
		}
	}

	pre[class*='language-'] {
		position: relative;
		overflow: auto;

		/* make space  */
		margin: 0.3125rem 0;
		padding: 1.75rem 0 1.75rem 1rem;
		border-radius: 0.625rem;
	}

	pre[class*='language-'] button {
		position: absolute;
		top: 0.3125rem;
		right: 0.3125rem;

		font-size: 0.9rem;
		padding: 0.15rem;
		background-color: #828282;

		border: ridge 1px #7b7b7c;
		border-radius: 0.3125rem;
		text-shadow: #c4c4c4 0 0 2px;
	}

	pre[class*='language-'] button:hover {
		cursor: pointer;
		background-color: #bcbabb;
	}

	:global(a[href*='#codex:']) {
		text-decoration-skip-ink: none !important;
		cursor: pointer;
	}
</style>
