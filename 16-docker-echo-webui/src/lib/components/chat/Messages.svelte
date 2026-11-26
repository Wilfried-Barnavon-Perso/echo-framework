<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { config, settings, user as _user, mobile, temporaryChatEnabled } from '$lib/stores';
	import { refreshChatList } from '$lib/stores/chatList';
	import { tick, getContext, onMount, onDestroy, createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	import { toast } from 'svelte-sonner';
	import { deleteChatMessageById, getChatById, updateChatById } from '$lib/apis/chats';
	import type { EchoCompactionCheckpoint } from '$lib/apis/echo/chat_actions';
	import {
		appendChildToWindowedHistory,
		copyToClipboard,
		extractCurlyBraceWords,
		getBranchSiblingIds,
		getDeepestChildId,
		getDeepestTreeChildId,
		sanitizeHistory
	} from '$lib/utils';
	import { getOutputText } from './Messages/structuredOutput';

	import EchoCompactionBoundary from './Messages/EchoCompactionBoundary.svelte';
	import Message from './Messages/Message.svelte';
	import Loader from '../common/Loader.svelte';
	import Spinner from '../common/Spinner.svelte';

	import ChatPlaceholder from './ChatPlaceholder.svelte';

	const i18n = getContext('i18n');

	export let className = 'h-full flex pt-18';

	export let chatId = '';
	export let user = $_user;

	export let prompt;
	export let history = {};
	export let selectedModels;
	export let atSelectedModel;

	let messages = [];

	export let setInputText: Function = () => {};

	export let sendMessage: Function;
	export let continueResponse: Function;
	export let regenerateResponse: Function;
	export let mergeResponses: Function;

	export let chatActionHandler: Function;
	export let showMessage: Function = () => {};
	export let submitMessage: Function = () => {};
	export let addMessages: Function = () => {};
	export let onToolCallResolved: Function = () => {};
	export let forkHandler: Function | null = null;

	export let readOnly = false;
	export let shareMode: 'continue' | null = null;
	export let allowDelete = true;
	export let allowToolApproval: boolean = false;
	export let compactPreview = false;
	export let editCodeBlock = true;

	export let topPadding = false;
	export let bottomPadding = false;
	export let autoScroll;
	export let messagesContainerId = 'messages-container';

	export let onSelect = (e) => {};
	export let onInsertToNote: ((content: string) => void) | null = null;

	export let messagesCount: number | null = 8;
	export let treeIndex: Record<string, any> | null = null;
	export let windowMeta: Record<string, any> | null = null;
	export let checkpoints: EchoCompactionCheckpoint[] = [];
	export let onLoadOlderWindow:
		| ((opts?: { beforeId?: string; toCheckpointId?: string }) => Promise<void>)
		| null = null;
	export let onEnsureBranchLoaded: ((targetId: string) => Promise<void>) | null = null;
	export let ensureMessageDetails: ((id: string) => Promise<void>) | null = null;
	let messagesLoading = false;
	let loadingOlderWindow = false;
	let lastLoadedChatId = chatId;

	$: checkpointByPivotId = new Map(
		(checkpoints ?? [])
			.filter((ckpt) => ckpt?.pivot_msg_id)
			.map((ckpt) => [String(ckpt.pivot_msg_id), ckpt])
	);
	$: hasUnrenderedLocalOlder = Boolean(
		messages.at(0)?.parentId && history.messages?.[messages.at(0).parentId]
	);

	const handleLoadOlderWindow = async (ckpt?: EchoCompactionCheckpoint | null) => {
		if (loadingOlderWindow || !onLoadOlderWindow) return;
		const element = getMessagesContainer();
		const firstMsgEl = element?.querySelector('.message-listitem') as HTMLElement | null;
		const previousTop = firstMsgEl?.getBoundingClientRect().top ?? 0;
		loadingOlderWindow = true;
		try {
			await onLoadOlderWindow({
				beforeId: windowMeta?.oldest_loaded_id ?? messages[0]?.id ?? undefined,
				toCheckpointId: ckpt?.checkpoint_id
			});
			messagesCount = (messagesCount ?? 8) + 8;
			buildMessages();
			await tick();
			if (element && firstMsgEl?.isConnected) {
				element.scrollTop += firstMsgEl.getBoundingClientRect().top - previousTop;
			}
		} catch (error) {
			console.error('[Messages] handleLoadOlderWindow failed', error);
			toast.error(`${error}`);
		} finally {
			loadingOlderWindow = false;
		}
	};

	const getMessagesContainer = () => document.getElementById(messagesContainerId);

	onDestroy(() => {
		cancelAnimationFrame(pendingRebuild);
	});

	const loadMoreMessages = async () => {
		const element = getMessagesContainer();
		// Anchor to the current top message: content-visibility uses intrinsic
		// size estimates for off-screen items, so scrollHeight deltas drift.
		const firstMessage = element?.querySelector('.message-listitem') as HTMLElement | null;
		const previousTop = firstMessage?.getBoundingClientRect().top ?? 0;

		messagesLoading = true;
		messagesCount += 8;

		buildMessages();

		await tick();

		if (element && firstMessage?.isConnected) {
			element.scrollTop += firstMessage.getBoundingClientRect().top - previousTop;
		}

		messagesLoading = false;
	};

	let pendingRebuild = null;
	let lastCurrentId = null;

	$: if (chatId !== lastLoadedChatId) {
		lastLoadedChatId = chatId;
		messagesCount = 8;
	}

	const buildMessages = () => {
		let _messages = [];

		let message = history.messages[history.currentId];
		const visitedMessageIds = new Set();

		while (message && (messagesCount !== null ? _messages.length < messagesCount : true)) {
			if (visitedMessageIds.has(message.id)) {
				console.warn('Circular dependency detected in message history', message.id);
				break;
			}
			visitedMessageIds.add(message.id);

			_messages.push(message);
			message = message.parentId !== null ? history.messages[message.parentId] : null;
		}

		messages = _messages.reverse();
	};

	// Throttle message list rebuilds to once per animation frame during streaming.
	// Structural changes (currentId change) always rebuild immediately.
	const handleHistoryChange = (currentId, _messages) => {
		if (!currentId) {
			messages = [];
			return;
		}

		const currentIdChanged = currentId !== lastCurrentId;
		lastCurrentId = currentId;

		if (currentIdChanged) {
			// Structural change: new chat, navigation, new message — rebuild immediately
			cancelAnimationFrame(pendingRebuild);
			pendingRebuild = null;
			buildMessages();
		} else if (_messages) {
			// Content update (streaming) — throttle to once per frame
			if (!pendingRebuild) {
				pendingRebuild = requestAnimationFrame(() => {
					pendingRebuild = null;
					buildMessages();
				});
			}
		}
	};

	$: handleHistoryChange(history.currentId, history.messages);

	$: if (autoScroll && bottomPadding) {
		(async () => {
			await tick();
			scrollToBottom();
		})();
	}

	export const scrollToBottom = () => {
		const element = getMessagesContainer();
		if (element) {
			element.scrollTop = element.scrollHeight;

			// Re-scroll across two frames as content-visibility resolves message heights.
			requestAnimationFrame(() => {
				if (element) {
					element.scrollTop = element.scrollHeight;
					requestAnimationFrame(() => {
						element.scrollTop = element.scrollHeight;
					});
				}
			});
		}
	};

	export const scrollToMessage = async (id: string) => {
		messagesCount = null;
		buildMessages();
		await tick();
		const element = getMessagesContainer()?.querySelector<HTMLElement>(
			`#${CSS.escape(`message-${id}`)}`
		);
		element?.scrollIntoView({ block: 'center' });
		element?.animate([{ backgroundColor: '#a5a5c544' }, { backgroundColor: 'transparent' }], {
			duration: 2200
		});
	};

	export const scrollToTop = async () => {
		messagesCount = null;
		buildMessages();
		await tick();

		const element = getMessagesContainer();
		if (!element) return;

		element.scrollTo({ top: 0, behavior: 'smooth' });
		requestAnimationFrame(() => {
			element.scrollTo({ top: 0, behavior: 'smooth' });
			requestAnimationFrame(() => {
				element.scrollTo({ top: 0, behavior: 'smooth' });
			});
		});
	};

	const updateChat = async () => {
		if (!$temporaryChatEnabled) {
			history = history;
			await tick();
			const res = await updateChatById(
				localStorage.token,
				chatId,
				{
					history: history
				},
				null,
				{ light: true }
			);

			// Keep local plain-content edits aligned with the saved chat response.
			if (res?.chat?.history?.messages) {
				for (const [id, msg] of Object.entries(res.chat.history.messages)) {
					if (history.messages[id] && (msg as any).content) {
						history.messages[id].content = (msg as any).content;
					}
				}
				history = history;
			}

			await refreshChatList(localStorage.token);
		}
	};

	const navigateToSibling = async (
		message: any,
		targetMessageId: string | undefined,
		focusSelector?: string
	) => {
		if (targetMessageId && message.id !== targetMessageId) {
			const leafId = getDeepestTreeChildId(history, treeIndex, targetMessageId) ?? targetMessageId;
			if ((!history.messages[leafId] || !history.messages[targetMessageId]) && onEnsureBranchLoaded) {
				await onEnsureBranchLoaded(leafId);
			}
			const childId = getDeepestChildId(history, targetMessageId);
			if (childId && history.messages[childId]) {
				history.currentId = childId;
			}
		}

		await tick();

		if (focusSelector) {
			document.querySelector<HTMLElement>(focusSelector)?.focus();
		}

		if ($settings?.scrollOnBranchChange ?? true) {
			const element = getMessagesContainer();
			autoScroll = element
				? element.scrollHeight - element.scrollTop <= element.clientHeight + 50
				: false;

			setTimeout(() => {
				scrollToBottom();
			}, 100);
		}
	};

	const gotoMessage = async (message, idx) => {
		const siblings = getBranchSiblingIds(history, treeIndex, message);
		if (!siblings.length) return;

		idx = Math.max(0, Math.min(idx, siblings.length - 1));
		const messageId = siblings[idx];
		await navigateToSibling(message, messageId);
	};

	const showPreviousMessage = async (message) => {
		const refocus = document.activeElement?.classList.contains('previous-message-button');
		const siblings = getBranchSiblingIds(history, treeIndex, message);
		if (!siblings.length) return;

		const currentIdx = siblings.indexOf(message.id);
		const messageId = siblings[Math.max((currentIdx >= 0 ? currentIdx : 0) - 1, 0)];
		await navigateToSibling(
			message,
			messageId,
			refocus ? `#message-${messageId} .previous-message-button` : undefined
		);
	};

	const showNextMessage = async (message) => {
		const refocus = document.activeElement?.classList.contains('next-message-button');
		const siblings = getBranchSiblingIds(history, treeIndex, message);
		if (!siblings.length) return;

		const currentIdx = siblings.indexOf(message.id);
		const messageId =
			siblings[Math.min((currentIdx >= 0 ? currentIdx : 0) + 1, siblings.length - 1)];
		await navigateToSibling(
			message,
			messageId,
			refocus ? `#message-${messageId} .next-message-button` : undefined
		);
	};

	const rateMessage = async (messageId, rating) => {
		history.messages[messageId].annotation = {
			...history.messages[messageId].annotation,
			rating: rating
		};

		await updateChat();
	};

	const editMessage = async (messageId, { content, files, output = undefined }, submit = true) => {
		if ((selectedModels ?? []).filter((id) => id).length === 0) {
			toast.error($i18n.t('Model not selected'));
			return;
		}
		if (history.messages[messageId].role === 'user') {
			if (submit) {
				// New user message
				let userPrompt = content;
				let userMessageId = uuidv4();

				let userMessage = {
					id: userMessageId,
					parentId: history.messages[messageId].parentId,
					childrenIds: [],
					role: 'user',
					user_id: $_user?.id,
					user: { id: $_user?.id, name: $_user?.name },
					content: userPrompt,
					...(files && { files: files }),
					models: selectedModels,
					timestamp: Math.floor(Date.now() / 1000) // Unix epoch
				};

				let messageParentId = history.messages[messageId].parentId;

				if (messageParentId !== null) {
					appendChildToWindowedHistory(history, treeIndex, messageParentId, userMessageId);
				}

				history.messages[userMessageId] = userMessage;
				history.currentId = userMessageId;

				await tick();
				await sendMessage(history, userMessageId);
			} else {
				// Edit user message
				history.messages[messageId].content = content;
				history.messages[messageId].files = files;
				await updateChat();
			}
		} else {
			if (submit) {
				// New response message (Save As Copy)
				const responseMessageId = uuidv4();
				const message = history.messages[messageId];
				const parentId = message.parentId;

				const responseMessage = {
					...message,
					id: responseMessageId,
					parentId: parentId,
					childrenIds: [],
					files: undefined,
					annotation: undefined,
					feedbackId: undefined,
					content: output !== undefined ? getOutputText(output) : content,
					...(output !== undefined ? { output } : {}),
					timestamp: Math.floor(Date.now() / 1000) // Unix epoch
				};

				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null) {
					appendChildToWindowedHistory(history, treeIndex, parentId, responseMessageId);
				}

				await updateChat();
			} else {
				// Edit response message
				if (content !== undefined) {
					history.messages[messageId].content = content;
				}
				if (output !== undefined) {
					history.messages[messageId].output = output;
					history.messages[messageId].content = getOutputText(output);
				}
				await updateChat();
			}
		}
	};

	const actionMessage = async (actionId, message, event = null) => {
		await chatActionHandler(chatId, actionId, message.model, message.id, event);
	};

	const saveMessage = async (messageId, message) => {
		if (!history.messages?.[messageId]) {
			return;
		}

		history.messages[messageId] = message;
		await updateChat();
	};

	const deleteMessage = async (messageId) => {
		const messageToDelete = history.messages[messageId];
		if (!messageToDelete) return;
		const parentMessageId = messageToDelete.parentId;
		const childMessageIds = messageToDelete.childrenIds ?? [];

		if (parentMessageId && !history.messages[parentMessageId] && onLoadOlderWindow) {
			await onLoadOlderWindow({ beforeId: messageId }).catch(() => {});
		}

		// Collect all grandchildren
		const grandchildrenIds = childMessageIds.flatMap(
			(childId) => history.messages[childId]?.childrenIds ?? treeIndex?.[childId]?.childrenIds ?? []
		);

		// Update parent's children
		if (parentMessageId && history.messages[parentMessageId]) {
			history.messages[parentMessageId].childrenIds = [
				...history.messages[parentMessageId].childrenIds.filter((id) => id !== messageId),
				...grandchildrenIds
			];
		}

		// Update grandchildren's parent
		grandchildrenIds.forEach((grandchildId) => {
			if (history.messages[grandchildId]) {
				history.messages[grandchildId].parentId = parentMessageId;
			}
			if (treeIndex?.[grandchildId]) {
				treeIndex[grandchildId].parentId = parentMessageId;
			}
		});

		// Delete the message and its children
		[messageId, ...childMessageIds].forEach((id) => {
			delete history.messages[id];
			if (treeIndex) delete treeIndex[id];
		});
		if (treeIndex && parentMessageId && treeIndex[parentMessageId]) {
			treeIndex[parentMessageId].childrenIds = [
				...(treeIndex[parentMessageId].childrenIds ?? []).filter((id: string) => id !== messageId),
				...grandchildrenIds
			];
		}

		const candidateCurrentId =
			parentMessageId === null || history.messages[parentMessageId]
				? getDeepestChildId(history, parentMessageId)
				: (grandchildrenIds.find((id) => Boolean(history.messages[id])) ?? null);
		history.currentId =
			candidateCurrentId && history.messages[candidateCurrentId]
				? candidateCurrentId
				: (Object.keys(history.messages).at(-1) ?? null);
		history = history;

		if (!$temporaryChatEnabled) {
			await deleteChatMessageById(localStorage.token, chatId, messageId);
			const fresh = await getChatById(localStorage.token, chatId, { windowed: true }).catch(
				() => null
			);
			if (fresh?.chat?.history) {
				history = fresh.chat.history;
				if (fresh.tree_index) treeIndex = fresh.tree_index;
				if (fresh.window_meta) windowMeta = fresh.window_meta;
				sanitizeHistory(history, treeIndex);
			}

			await refreshChatList(localStorage.token);
		}
	};

	const triggerScroll = () => {
		if (autoScroll) {
			const element = getMessagesContainer();
			if (element) {
				autoScroll = element.scrollHeight - element.scrollTop <= element.clientHeight + 50;
				setTimeout(() => {
					scrollToBottom();
				}, 100);
			}
		}
	};
</script>

<div class={className}>
	{#if Object.keys(history?.messages ?? {}).length == 0}
		<ChatPlaceholder modelIds={selectedModels} {atSelectedModel} {onSelect} />
	{:else}
		<div class="w-full pt-2">
			{#key chatId}
				<section class="w-full" aria-labelledby="chat-conversation">
					<h2 class="sr-only" id="chat-conversation">{$i18n.t('Chat Conversation')}</h2>
					{#if hasUnrenderedLocalOlder}
						<Loader
							pollIntervalMs={250}
							on:visible={(e) => {
								console.log('visible');
								if (!messagesLoading) {
									loadMoreMessages();
								}
							}}
						>
							<div class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2">
								<Spinner className=" size-4" />
								<div class=" ">{$i18n.t('Loading...')}</div>
							</div>
						</Loader>
					{:else if windowMeta?.has_older}
						<EchoCompactionBoundary
							checkpoint={checkpointByPivotId.get(String(messages.at(0)?.id ?? '')) ?? null}
							hasOlder={true}
							loading={loadingOlderWindow}
							onLoadOlder={handleLoadOlderWindow}
						/>
					{/if}
					<div role="log" aria-live="polite" aria-relevant="additions" aria-atomic="false">
						<ul>
							{#each messages as message, messageIdx (message.id)}
								{@const boundaryCheckpoint = !(
									messageIdx === 0 &&
									!hasUnrenderedLocalOlder &&
									windowMeta?.has_older
								)
									? (checkpointByPivotId.get(String(message.id)) ?? null)
									: null}
								{#if boundaryCheckpoint}
									<EchoCompactionBoundary checkpoint={boundaryCheckpoint} hasOlder={false} />
								{/if}
								<Message
									{chatId}
									bind:history
									{treeIndex}
									{ensureMessageDetails}
									selectedModels={atSelectedModel ? [atSelectedModel.id] : selectedModels}
									messageId={message.id}
									idx={messageIdx}
									{user}
									{setInputText}
									{gotoMessage}
									{showPreviousMessage}
									{showNextMessage}
									{updateChat}
									{editMessage}
									{deleteMessage}
									{rateMessage}
									{actionMessage}
									{saveMessage}
									{submitMessage}
									{regenerateResponse}
									{continueResponse}
									{mergeResponses}
									{addMessages}
									{onToolCallResolved}
									{forkHandler}
									{allowDelete}
									{allowToolApproval}
									{triggerScroll}
									{readOnly}
									{compactPreview}
									{editCodeBlock}
									{topPadding}
									{onInsertToNote}
									{shareMode}
								/>
							{/each}
						</ul>
					</div>
				</section>
				<div class="pb-18" />
				{#if bottomPadding}
					<div class="  pb-6" />
				{/if}
			{/key}
		</div>
	{/if}
</div>
