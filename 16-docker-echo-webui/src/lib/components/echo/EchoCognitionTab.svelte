<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		postEchoSubagentControl,
		postEchoSubagentMessage,
		type EchoSubagentControlAction
	} from '$lib/apis/echo';
	import {
		acquireCognitionStream,
		echoCognitionSelectedSubSid,
		echoCognitionSnapshot,
		getEchoModelBadge,
		refreshCognitionSnapshot,
		type EchoCognitionGroup,
		type EchoCognitionQueuedMsg,
		type EchoCognitionStep
	} from '$lib/stores/echo';
	import { isSavedChatId } from '$lib/utils/chatId';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let chatId: string = '';

	let mounted = false;
	let boundChatId = '';
	let releaseStream: (() => void) | null = null;
	let initialLoading = false;
	let refreshing = false;
	let activeTab: 'threads' | 'tools' = 'threads';
	let composerText = '';
	let sending = false;
	let controlActionPending: EchoSubagentControlAction | null = null;
	let feedContainer: HTMLDivElement | null = null;
	let lastScrolledKey = '';

	let selectedGroupId = '';
	let participantFilter = 'all';
	let composerTargetSubSid = '';
	let autoFollow = true;
	let manualPause = false;
	let isProgrammaticScroll = false;
	let pausedAtStepCount = 0;
	let pausedNewSteps = 0;

	const syncChatStream = async (cid: string) => {
		const normalized = cid && isSavedChatId(cid) ? cid : '';
		if (normalized === boundChatId) return;

		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = normalized;
		selectedGroupId = '';
		participantFilter = 'all';
		composerTargetSubSid = '';
		autoFollow = true;
		manualPause = false;
		pausedNewSteps = 0;

		if (!normalized) {
			initialLoading = false;
			return;
		}

		initialLoading = !$echoCognitionSnapshot || $echoCognitionSnapshot.chat_id !== normalized;
		releaseStream = acquireCognitionStream(normalized);
		await refreshCognitionSnapshot(normalized);
		initialLoading = false;
	};

	onMount(() => {
		mounted = true;
		void syncChatStream(chatId);
	});

	$: if (mounted) {
		void syncChatStream(chatId);
	}

	onDestroy(() => {
		if (releaseStream) {
			releaseStream();
			releaseStream = null;
		}
		boundChatId = '';
	});

	const handleManualRefresh = async () => {
		if (!chatId || !isSavedChatId(chatId) || refreshing) return;
		refreshing = true;
		try {
			await refreshCognitionSnapshot(chatId);
		} finally {
			refreshing = false;
		}
	};

	$: snapshot = $echoCognitionSnapshot;
	$: threads = snapshot?.threads ?? {};
	$: threadMeta = snapshot?.thread_meta ?? {};
	$: queuedMap = snapshot?.queued_messages ?? {};
	$: toolCalls = snapshot?.tool_calls ?? [];

	const getThreadUpdatedAt = (sid: string): number => {
		const metaTs = Number(threadMeta[sid]?.updated_at ?? 0) || 0;
		const steps = threads[sid] ?? [];
		const lastStepTs = steps.length > 0 ? Number(steps[steps.length - 1]?.updated_at ?? 0) || 0 : 0;
		return Math.max(metaTs, lastStepTs);
	};

	$: sortedSubSids = Array.from(new Set([...Object.keys(threads), ...Object.keys(threadMeta)])).sort(
		(a, b) => {
			const aRunning = threadMeta[a]?.status === 'running' ? 1 : 0;
			const bRunning = threadMeta[b]?.status === 'running' ? 1 : 0;
			if (aRunning !== bRunning) return bRunning - aRunning;
			return getThreadUpdatedAt(b) - getThreadUpdatedAt(a);
		}
	);

	const isStepPendingTools = (step?: EchoCognitionStep | null): boolean => {
		if (!step) return false;
		if (Array.isArray(step.pending_tool_calls)) return step.pending_tool_calls.length > 0;
		return Boolean(step.pending_tool_calls);
	};

	interface StatusUiSpec {
		label: string;
		shortLabel: string;
		badgeClass: string;
		dotClass: string;
		pulse: boolean;
	}

	const STATUS_UI_MAP: Record<string, StatusUiSpec> = {
		running: {
			label: 'En cours',
			shortLabel: '⚡ En cours',
			badgeClass: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
			dotClass: 'bg-emerald-500',
			pulse: true
		},
		suspended_checkpoint: {
			label: 'Point de contrôle',
			shortLabel: '⏸ Point de contrôle',
			badgeClass: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30',
			dotClass: 'bg-amber-500',
			pulse: false
		},
		suspended_pare_alert: {
			label: 'Alerte PARE (3 erreurs)',
			shortLabel: '⚠️ Alerte PARE',
			badgeClass: 'bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30',
			dotClass: 'bg-red-500',
			pulse: false
		},
		pending_question: {
			label: 'Question en attente',
			shortLabel: '❓ Question',
			badgeClass: 'bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/40',
			dotClass: 'bg-amber-500',
			pulse: true
		},
		suspended: {
			label: 'En pause',
			shortLabel: '⏸ En pause',
			badgeClass: 'bg-slate-500/15 text-slate-700 dark:text-slate-300 border-slate-500/30',
			dotClass: 'bg-slate-400',
			pulse: false
		},
		killed: {
			label: 'Interrompu',
			shortLabel: '⛔ Interrompu',
			badgeClass: 'bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30',
			dotClass: 'bg-rose-500',
			pulse: false
		},
		error: {
			label: 'Erreur',
			shortLabel: '❌ Erreur',
			badgeClass: 'bg-red-600/15 text-red-700 dark:text-red-300 border-red-600/30',
			dotClass: 'bg-red-600',
			pulse: false
		},
		completed: {
			label: 'Terminé',
			shortLabel: '✅ Terminé',
			badgeClass: 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30',
			dotClass: 'bg-teal-500',
			pulse: false
		},
		idle: {
			label: 'En attente',
			shortLabel: 'En attente',
			badgeClass: 'bg-gray-200/70 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-300/60 dark:border-gray-700/60',
			dotClass: 'bg-indigo-400',
			pulse: false
		}
	};

	const STATUS_PRIORITY: Record<string, number> = {
		running: 9,
		suspended_pare_alert: 8,
		pending_question: 7,
		suspended_checkpoint: 6,
		suspended: 5,
		error: 4,
		killed: 3,
		completed: 2,
		idle: 1
	};

	const getStatusBadge = (status?: string | null): StatusUiSpec =>
		STATUS_UI_MAP[status || 'idle'] ?? STATUS_UI_MAP.idle;

	const normalizeGroup = (g: EchoCognitionGroup): Required<Pick<EchoCognitionGroup, 'id' | 'thread_type' | 'icon' | 'color' | 'display_label' | 'member_sids' | 'is_running' | 'updated_at'>> & EchoCognitionGroup => {
		const id = g.id || g.group_id || '';
		const member_sids = g.member_sids ?? g.threads ?? [];
		const model_keys = g.model_keys ?? Array.from(
			new Set(
				member_sids
					.map((sid) => threadMeta[sid]?.model_key)
					.filter((mk): mk is string => Boolean(mk))
			)
		);
		let group_status = g.group_status || '';
		if (!group_status) {
			group_status = member_sids.reduce((best, sid) => {
				const st = threadMeta[sid]?.status || 'idle';
				return (STATUS_PRIORITY[st] ?? 0) > (STATUS_PRIORITY[best] ?? 0) ? st : best;
			}, 'idle');
		}
		return {
			...g,
			id,
			thread_type: g.thread_type || g.group_type || 'agent',
			icon: g.icon || '🤖',
			color: g.color || 'indigo',
			display_label: g.display_label || g.label || id,
			member_sids,
			model_keys,
			group_status,
			is_running: Boolean(g.is_running),
			updated_at: Number(g.updated_at ?? 0) || 0
		};
	};

	// Utilise grouped_threads fourni par le backend ou replie sur les threads individuels
	$: groups = ((): ReturnType<typeof normalizeGroup>[] => {
		if (snapshot?.grouped_threads && snapshot.grouped_threads.length > 0) {
			return snapshot.grouped_threads.map(normalizeGroup);
		}
		return sortedSubSids.map((sid) => {
			const meta = threadMeta[sid] ?? {};
			const steps = threads[sid] ?? [];
			const role = meta.display_label || meta.role || steps.find((s) => s.role_id)?.role_id || sid;
			const isRunning = meta.is_running ?? meta.status === 'running';
			const modelKey = meta.model_key;
			return normalizeGroup({
				id: sid,
				thread_type: meta.thread_type || 'agent',
				icon: meta.icon || '🤖',
				color: meta.color || 'indigo',
				display_label: role,
				member_sids: [sid],
				participant_labels: { [sid]: role },
				model_keys: modelKey ? [modelKey] : [],
				is_running: isRunning,
				updated_at: getThreadUpdatedAt(sid),
				steps_count: steps.length,
				merged_steps: steps
			});
		});
	})();

	// Résolution automatique du groupe si echoCognitionSelectedSubSid cible un membre
	$: if (chatId && groups.length > 0) {
		const externalSid = $echoCognitionSelectedSubSid[chatId];
		if (externalSid) {
			const matchingGroup = groups.find(
				(g) => g.id === externalSid || g.member_sids.includes(externalSid)
			);
			if (matchingGroup && matchingGroup.id !== selectedGroupId) {
				selectedGroupId = matchingGroup.id;
				participantFilter = 'all';
				if (matchingGroup.member_sids.includes(externalSid)) {
					composerTargetSubSid = externalSid;
				}
			}
		}
		if (!selectedGroupId || !groups.some((g) => g.id === selectedGroupId)) {
			selectedGroupId = groups[0].id;
			participantFilter = 'all';
		}
	}

	$: selectedGroup = groups.find((g) => g.id === selectedGroupId) ?? groups[0] ?? null;

	$: if (selectedGroup) {
		const members = selectedGroup.member_sids;
		if (members.length > 0 && (!composerTargetSubSid || !members.includes(composerTargetSubSid))) {
			composerTargetSubSid = members[0];
		}
		if (participantFilter !== 'all' && !members.includes(participantFilter)) {
			participantFilter = 'all';
		}
	}

	$: rawGroupSteps = ((): EchoCognitionStep[] => {
		if (!selectedGroup) return [];
		if (selectedGroup.merged_steps && selectedGroup.merged_steps.length > 0) {
			return selectedGroup.merged_steps;
		}
		if (selectedGroup.member_sids.length === 1) {
			return threads[selectedGroup.member_sids[0]] ?? [];
		}
		const merged: EchoCognitionStep[] = [];
		for (const sid of selectedGroup.member_sids) {
			for (const st of threads[sid] ?? []) {
				merged.push(st);
			}
		}
		return merged.sort((a, b) => (Number(a.updated_at ?? 0) - Number(b.updated_at ?? 0)) || (a.step_index - b.step_index));
	})();

	$: displayedSteps =
		participantFilter === 'all'
			? rawGroupSteps
			: rawGroupSteps.filter((s) => s.sub_sid === participantFilter);

	$: selectedQueue = ((): { sid: string; msg: EchoCognitionQueuedMsg }[] => {
		if (!selectedGroup) return [];
		const items: { sid: string; msg: EchoCognitionQueuedMsg }[] = [];
		for (const sid of selectedGroup.member_sids) {
			for (const msg of queuedMap[sid] ?? []) {
				items.push({ sid, msg });
			}
		}
		return items;
	})();

	$: selectedIsRunning = selectedGroup?.is_running ?? false;
	$: targetIsRunning = composerTargetSubSid
		? threadMeta[composerTargetSubSid]?.status === 'running'
		: selectedIsRunning;
	$: activeThreadSid =
		participantFilter !== 'all'
			? participantFilter
			: selectedGroup?.member_sids.length === 1
				? selectedGroup.member_sids[0]
				: '';
	$: activeThreadMeta = activeThreadSid ? threadMeta[activeThreadSid] : undefined;
	$: activeStatus = activeThreadMeta?.status || selectedGroup?.group_status || (selectedIsRunning ? 'running' : 'idle');
	$: activeStatusBadge = getStatusBadge(activeStatus);
	$: activeSliceUsed = Number(activeThreadMeta?.slice_calls_used ?? 0);
	$: activeSliceLimit = Number(activeThreadMeta?.slice_calls_limit ?? 8);
	$: activeTotalUsed = Number(activeThreadMeta?.total_calls_used ?? 0);
	$: activeConsecutiveErrors = Number(activeThreadMeta?.consecutive_errors ?? 0);
	$: activeCheckpointReason = activeThreadMeta?.checkpoint_reason ?? null;
	$: controlTargetId = activeThreadSid || selectedGroup?.id || '';
	$: canResumeStatus = ['suspended', 'suspended_checkpoint', 'suspended_pare_alert', 'idle', 'pending_question'].includes(activeStatus);

	const getGroupQueueCount = (g: ReturnType<typeof normalizeGroup>): number =>
		g.member_sids.reduce((acc, sid) => acc + (queuedMap[sid]?.length ?? 0), 0);

	const getGroupOptionLabel = (g: ReturnType<typeof normalizeGroup>): string => {
		const statusLabel = getStatusBadge(g.group_status).shortLabel;
		const queueLen = getGroupQueueCount(g);
		const queueBadge = queueLen > 0 ? ` (+${queueLen} en attente)` : '';
		return `${g.icon} ${g.display_label} — ${statusLabel}${queueBadge}`;
	};

	const getParticipantLabel = (sid: string, step?: EchoCognitionStep): string => {
		if (step?.participant_label) return step.participant_label;
		if (selectedGroup?.participant_labels?.[sid]) return selectedGroup.participant_labels[sid];
		const meta = threadMeta[sid];
		return meta?.display_label || meta?.role || step?.role_id || sid;
	};

	const onSelectGroup = (e: Event) => {
		const target = e.currentTarget as HTMLSelectElement;
		if (!chatId || !target.value) return;
		const group = groups.find((g) => g.id === target.value);
		if (!group) return;
		selectedGroupId = group.id;
		participantFilter = 'all';
		composerTargetSubSid = group.member_sids[0] ?? '';
		autoFollow = true;
		manualPause = false;
		pausedNewSteps = 0;
		echoCognitionSelectedSubSid.update((curr) => ({
			...curr,
			[chatId]: group.member_sids[0] ?? group.id
		}));
	};

	const ACCENT_CLASSES: Record<string, string> = {
		cyan: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
		purple: 'border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-300',
		amber: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
		emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
		indigo: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300'
	};

	const getAccentClass = (color?: string): string =>
		ACCENT_CLASSES[color ?? 'indigo'] ?? ACCENT_CLASSES.indigo;

	interface ParsedStepView {
		thoughtTexts: string[];
		visibleTexts: string[];
		toolCalls: { name: string; args?: any }[];
		toolResponses: { name: string; response?: any }[];
		domMapCounts: number[];
		fallbackRaw: string;
	}

	const extractDomMapGuards = (raw: string): { cleanText: string; domCounts: number[] } => {
		const domCounts: number[] = [];
		let cleanText = raw;

		// 1. Marqueur explicite de Carte DOM injectée par le navigateur
		const domHeaderRegex =
			/Voici les éléments interactifs actuels \(Carte DOM\)\s*:\s*(\[[\s\S]*?\]|\{[\s\S]*?\})/gi;
		cleanText = cleanText.replace(domHeaderRegex, (_match, jsonPart) => {
			try {
				const parsed = JSON.parse(jsonPart);
				const count = Array.isArray(parsed)
					? parsed.length
					: Array.isArray(parsed?.dom_map)
						? parsed.dom_map.length
						: 0;
				domCounts.push(count);
			} catch {
				domCounts.push(0);
			}
			return '';
		});

		// 2. Objet JSON brut contenant "dom_map": [...]
		const rawDomMapRegex = /\{\s*"dom_map"\s*:\s*(\[[\s\S]*?\])[\s\S]*?\}/g;
		cleanText = cleanText.replace(rawDomMapRegex, (_match, arrPart) => {
			try {
				const parsed = JSON.parse(arrPart);
				domCounts.push(Array.isArray(parsed) ? parsed.length : 0);
			} catch {
				domCounts.push(0);
			}
			return '';
		});

		return { cleanText: cleanText.trim(), domCounts };
	};

	const pushCleanVisibleText = (result: ParsedStepView, text: string) => {
		const { cleanText, domCounts } = extractDomMapGuards(text);
		if (domCounts.length > 0) {
			result.domMapCounts.push(...domCounts);
		}
		if (cleanText) {
			result.visibleTexts.push(cleanText);
		}
	};

	const parseStepParts = (step: EchoCognitionStep): ParsedStepView => {
		const result: ParsedStepView = {
			thoughtTexts: [],
			visibleTexts: [],
			toolCalls: [],
			toolResponses: [],
			domMapCounts: [],
			fallbackRaw: ''
		};

		const content = step.content;
		if (Array.isArray(content)) {
			for (const part of content) {
				if (!part || typeof part !== 'object') {
					if (typeof part === 'string' && part.trim()) {
						pushCleanVisibleText(result, part);
					}
					continue;
				}
				if (typeof part.text === 'string' && part.text.trim()) {
					if (part.thought) {
						result.thoughtTexts.push(part.text.trim());
					} else {
						pushCleanVisibleText(result, part.text);
					}
				}
				if (part.functionCall && typeof part.functionCall === 'object') {
					result.toolCalls.push({
						name: part.functionCall.name || 'outil',
						args: part.functionCall.args
					});
				}
				if (part.functionResponse && typeof part.functionResponse === 'object') {
					result.toolResponses.push({
						name: part.functionResponse.name || 'outil',
						response: part.functionResponse.response
					});
				}
			}
		} else if (typeof content === 'string') {
			pushCleanVisibleText(result, content);
		} else if (content && typeof content === 'object') {
			if (Array.isArray((content as any).dom_map)) {
				result.domMapCounts.push((content as any).dom_map.length);
			} else {
				result.fallbackRaw = JSON.stringify(content, null, 2);
			}
		}

		return result;
	};

	const formatTimestamp = (ts: any): string => {
		if (!ts) return '';
		const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
		if (Number.isNaN(d.getTime())) return '';
		return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	};

	const scrollToBottom = async (smooth = true) => {
		isProgrammaticScroll = true;
		await tick();
		if (feedContainer) {
			feedContainer.scrollTo({
				top: feedContainer.scrollHeight,
				behavior: smooth ? 'smooth' : 'auto'
			});
		}
		setTimeout(() => {
			isProgrammaticScroll = false;
		}, smooth ? 250 : 50);
	};

	const handleFeedScroll = () => {
		if (!feedContainer || isProgrammaticScroll) return;
		const distanceToBottom =
			feedContainer.scrollHeight - feedContainer.scrollTop - feedContainer.clientHeight;
		if (distanceToBottom > 80) {
			if (autoFollow) {
				autoFollow = false;
				pausedAtStepCount = displayedSteps.length;
			}
		} else if (!manualPause && !autoFollow) {
			autoFollow = true;
			pausedNewSteps = 0;
		}
	};

	const pauseAutoFollow = () => {
		manualPause = true;
		autoFollow = false;
		pausedAtStepCount = displayedSteps.length;
		pausedNewSteps = 0;
	};

	const resumeAutoFollow = () => {
		manualPause = false;
		autoFollow = true;
		pausedNewSteps = 0;
		void scrollToBottom(true);
	};

	$: {
		const lastStep = displayedSteps[displayedSteps.length - 1];
		const scrollKey = `${selectedGroupId}:${participantFilter}:${displayedSteps.length}:${isStepPendingTools(lastStep) ? '1' : '0'}:${selectedQueue.length}:${toolCalls.length}`;
		if (activeTab === 'threads' && selectedGroupId && scrollKey !== lastScrolledKey) {
			lastScrolledKey = scrollKey;
			if (autoFollow) {
				void scrollToBottom(true);
			} else {
				pausedNewSteps = Math.max(0, displayedSteps.length - pausedAtStepCount);
			}
		}
	}

	const sendMessage = async () => {
		const text = composerText.trim();
		const targetSid = composerTargetSubSid || selectedGroup?.member_sids[0] || '';
		if (!text || !chatId || !isSavedChatId(chatId) || !targetSid || sending) return;
		const token = localStorage?.token;
		if (!token) {
			toast.error('Session expirée : veuillez vous reconnecter.');
			return;
		}

		sending = true;
		const previousText = composerText;
		composerText = '';
		try {
			const res = await postEchoSubagentMessage(token, chatId, targetSid, text);
			if (res.status === 'queued') {
				const count = res.queue_length ?? 1;
				toast.info(`Message mis en file pour le prochain tour (${count} en attente).`);
			} else {
				toast.success('Message envoyé au sous-agent.');
			}
			autoFollow = true;
			manualPause = false;
			pausedNewSteps = 0;
			await refreshCognitionSnapshot(chatId);
			await scrollToBottom(true);
		} catch (e: any) {
			composerText = previousText;
			toast.error(e?.message || "Impossible d'envoyer le message au sous-agent.");
		} finally {
			sending = false;
		}
	};

	const handleComposerKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			void sendMessage();
		}
	};

	const CONTROL_TOAST_MAP: Record<EchoSubagentControlAction, string> = {
		suspend: 'Pause demandée (arrêt en fin de tour d’outil).',
		resume: 'Nouvelle tranche (+8 appels) lancée.',
		kill: 'Sous-agent interrompu et scellé.',
		close: 'Session sous-agent fermée et purgée.'
	};

	const runSubagentControl = async (action: EchoSubagentControlAction) => {
		if (!chatId || !isSavedChatId(chatId) || !controlTargetId || controlActionPending) return;
		const token = localStorage?.token;
		if (!token) {
			toast.error('Session expirée : veuillez vous reconnecter.');
			return;
		}

		controlActionPending = action;
		try {
			await postEchoSubagentControl(token, chatId, controlTargetId, { action });
			toast.success(CONTROL_TOAST_MAP[action]);
			if (action === 'close' && controlTargetId === selectedGroupId) {
				selectedGroupId = '';
			}
			await refreshCognitionSnapshot(chatId);
		} catch (e: any) {
			toast.error(e?.message || 'Action impossible sur le sous-agent.');
		} finally {
			controlActionPending = null;
		}
	};
</script>

<div class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden">
	<!-- Barre de contrôle supérieure -->
	<div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0">
		<div class="flex flex-wrap items-center gap-2 min-w-0">
			<span class="font-semibold text-sm text-gray-900 dark:text-white flex items-center gap-1.5 whitespace-nowrap shrink-0">
				<svg class="w-4 h-4 text-indigo-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
					<circle cx="12" cy="6" r="3" />
					<circle cx="6" cy="18" r="3" />
					<circle cx="18" cy="18" r="3" />
					<path d="M10.5 8.5L7.5 15.5M13.5 8.5l3 7" />
				</svg>
				Sous-agents
			</span>
			<span class="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-mono font-medium shrink-0">
				{groups.length} • {toolCalls.length} outil(s)
			</span>
			{#if activeTab === 'threads' && selectedGroup}
				<div
					role="group"
					aria-label="Contrôle du suivi temps réel"
					class="inline-flex items-center rounded-full bg-gray-200/80 dark:bg-gray-800 p-0.5 border border-gray-300/60 dark:border-gray-700/60 shrink-0"
				>
					<button
						type="button"
						on:click={resumeAutoFollow}
						class="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full transition {autoFollow
							? 'bg-white dark:bg-gray-700 text-red-600 dark:text-red-400 shadow-xs'
							: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
						title="Activer le suivi temps réel"
					>
						🔴 Live
					</button>
					<button
						type="button"
						on:click={pauseAutoFollow}
						class="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full transition {!autoFollow
							? 'bg-white dark:bg-gray-700 text-amber-700 dark:text-amber-300 shadow-xs'
							: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
						title="Mettre en pause le défilement"
					>
						⏸ Pause
					</button>
				</div>
			{/if}
		</div>

		<div class="flex items-center gap-1 bg-gray-200/80 dark:bg-gray-800 p-0.5 rounded-lg text-xs font-medium shrink-0">
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'threads'
					? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white'
					: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
				on:click={() => (activeTab = 'threads')}
			>
				Conversations
			</button>
			<button
				type="button"
				class="px-2.5 py-1 rounded-md transition {activeTab === 'tools'
					? 'bg-white dark:bg-gray-700 shadow-sm text-gray-900 dark:text-white'
					: 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'}"
				on:click={() => (activeTab = 'tools')}
			>
				Appels d'outils
			</button>
			<button
				type="button"
				class="p-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 ml-0.5 disabled:opacity-40"
				title="Actualiser"
				disabled={!isSavedChatId(chatId) || refreshing}
				on:click={handleManualRefresh}
			>
				<svg
					class="w-3.5 h-3.5 {refreshing ? 'animate-spin' : ''}"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2"
				>
					<path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
				</svg>
			</button>
		</div>
	</div>

	{#if initialLoading}
		<div class="flex-1 flex items-center justify-center">
			<Spinner className="w-6 h-6 text-indigo-500" />
		</div>
	{:else if activeTab === 'threads'}
		{#if !isSavedChatId(chatId)}
			<div class="flex-1 flex flex-col items-center justify-center p-6 text-center text-gray-400 text-xs">
				Démarrez une conversation pour consulter et piloter les sous-agents de la session.
			</div>
		{:else if groups.length === 0}
			<div class="flex-1 flex flex-col items-center justify-center p-6 text-center text-gray-400 text-xs">
				<svg class="w-8 h-8 mb-2 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
					<circle cx="12" cy="12" r="10" />
					<path d="M12 16v-4M12 8h.01" />
				</svg>
				Aucun sous-agent actif ou enregistré pour cette conversation.
			</div>
		{:else}
			<!-- Sélecteur de groupe / sous-agent + méta live -->
			<div class="px-4 py-2.5 border-b border-gray-200 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-850/60 space-y-2 shrink-0">
				<div class="flex items-center gap-2.5">
					<span class="relative flex items-center justify-center w-2.5 h-2.5 shrink-0">
						{#if activeStatusBadge.pulse}
							<span class="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping {activeStatusBadge.dotClass}"></span>
							<span class="relative inline-flex rounded-full w-2 h-2 {activeStatusBadge.dotClass}"></span>
						{:else}
							<span class="relative inline-flex rounded-full w-2 h-2 {activeStatusBadge.dotClass}"></span>
						{/if}
					</span>

					<select
						aria-label="Sélectionner un groupe ou sous-agent"
						value={selectedGroupId}
						on:change={onSelectGroup}
						class="flex-1 min-w-0 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-xs font-medium text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
					>
						{#each groups as g (g.id)}
							<option value={g.id}>{getGroupOptionLabel(g)}</option>
						{/each}
					</select>

					{#if selectedQueue.length > 0}
						<span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 font-medium shrink-0">
							+{selectedQueue.length} en attente
						</span>
					{/if}
				</div>

				{#if selectedGroup}
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex flex-wrap items-center gap-1.5">
							<span class="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border {getAccentClass(selectedGroup.color)}">
								<span>{selectedGroup.icon}</span>
								<span class="truncate max-w-[200px]">{selectedGroup.display_label}</span>
							</span>
							<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-medium {activeStatusBadge.badgeClass}">
								{activeStatusBadge.label}
								{#if activeStatus === 'suspended_checkpoint' && activeThreadMeta}
									<span class="font-mono">({activeSliceUsed}/{activeSliceLimit})</span>
								{/if}
							</span>
							{#if activeThreadMeta}
								<span
									class="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full border border-gray-200 dark:border-gray-700 bg-white/70 dark:bg-gray-900/70 text-gray-600 dark:text-gray-300"
									title="Appels d'outils sur la tranche courante / cumul total"
								>
									{activeSliceUsed}/{activeSliceLimit} appels
									{#if activeTotalUsed > activeSliceUsed}
										<span class="text-gray-400">· total {activeTotalUsed}</span>
									{/if}
								</span>
							{/if}
							{#if activeConsecutiveErrors > 0}
								<span class="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400">
									{activeConsecutiveErrors}/3 err.
								</span>
							{/if}
							{#each selectedGroup.model_keys ?? [] as mk}
								{@const badge = getEchoModelBadge(mk)}
								{#if badge}
									<span class="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border {badge.badgeClass}">
										<span>{badge.icon}</span>
										<span>{badge.label}</span>
									</span>
								{/if}
							{/each}
						</div>

						<!-- Barre de pilotage temps réel -->
						<div class="flex items-center gap-1 shrink-0">
							{#if activeStatus === 'running'}
								<button
									type="button"
									disabled={Boolean(controlActionPending)}
									on:click={() => runSubagentControl('suspend')}
									class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 transition disabled:opacity-40"
									title="Mettre en pause propre à la fin du tour d'outil"
								>
									⏸ Pause
								</button>
								<button
									type="button"
									disabled={Boolean(controlActionPending)}
									on:click={() => runSubagentControl('kill')}
									class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium border border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300 hover:bg-rose-500/20 transition disabled:opacity-40"
									title="Interrompre immédiatement et sceller l'historique"
								>
									⏹ Stop
								</button>
							{:else}
								{#if canResumeStatus}
									<button
										type="button"
										disabled={Boolean(controlActionPending)}
										on:click={() => runSubagentControl('resume')}
										class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium border border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20 transition disabled:opacity-40"
										title="Accorder une nouvelle tranche supervisée (+8 appels)"
									>
										▶ Reprendre (+8)
									</button>
								{/if}
								<button
									type="button"
									disabled={Boolean(controlActionPending)}
									on:click={() => runSubagentControl('close')}
									class="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:border-rose-400 hover:text-rose-600 dark:hover:text-rose-400 transition disabled:opacity-40"
									title="Fermer et purger cette session sous-agent"
								>
									✕ Fermer
								</button>
							{/if}
						</div>
					</div>

					{#if activeStatus === 'suspended_checkpoint'}
						<div class="rounded-lg px-2.5 py-1.5 text-[11px] bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-300">
							Point de contrôle atteint ({activeCheckpointReason === 'timeout' ? 'délai 90s' : `${activeSliceUsed}/${activeSliceLimit} appels`}) — cliquez sur <strong>Reprendre (+8)</strong> ou envoyez une consigne.
						</div>
					{:else if activeStatus === 'suspended_pare_alert'}
						<div class="rounded-lg px-2.5 py-1.5 text-[11px] bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-300">
							Disjoncteur PARE déclenché (3 erreurs d'outils consécutives) — vérifiez les échanges avant de reprendre ou réorienter.
						</div>
					{:else if activeStatus === 'pending_question'}
						<div class="rounded-lg px-2.5 py-1.5 text-[11px] bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-300">
							Le sous-agent attend votre réponse ci-dessous pour poursuivre son exécution.
						</div>
					{/if}

					<!-- Filtre par participant pour les groupes multi-agents -->
					{#if selectedGroup.member_sids.length > 1}
						<div class="flex flex-wrap items-center gap-1 pt-0.5">
							<button
								type="button"
								on:click={() => (participantFilter = 'all')}
								class="text-[11px] px-2 py-0.5 rounded-full border transition {participantFilter === 'all'
									? 'bg-indigo-600 text-white border-indigo-600'
									: 'bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-indigo-400'}"
							>
								Tous ({rawGroupSteps.length})
							</button>
							{#each selectedGroup.member_sids as sid (sid)}
								<button
									type="button"
									on:click={() => (participantFilter = sid)}
									class="text-[11px] px-2 py-0.5 rounded-full border transition truncate max-w-[180px] {participantFilter === sid
										? 'bg-indigo-600 text-white border-indigo-600'
										: 'bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-indigo-400'}"
								>
									{getParticipantLabel(sid)}
								</button>
							{/each}
						</div>
					{/if}
				{/if}
			</div>

			<!-- Fil des échanges -->
			<div class="relative flex-1 min-h-0 flex flex-col">
				<div
					bind:this={feedContainer}
					on:scroll={handleFeedScroll}
					class="flex-1 overflow-y-auto p-4 space-y-3"
				>
					{#if displayedSteps.length === 0}
						<div class="flex items-center justify-center h-32 text-xs text-gray-400">
							Démarrage du sous-agent en cours…
						</div>
					{:else}
						{#each displayedSteps as step (`${step.sub_sid}:${step.step_index}`)}
							{@const parsed = parseStepParts(step)}
							{@const stepModelBadge = getEchoModelBadge(step.model_key || threadMeta[step.sub_sid]?.model_key)}
							{@const escalationBadge = getEchoModelBadge(step.escalation_target)}
							{@const isMultiMember = (selectedGroup?.member_sids.length ?? 0) > 1}
							{#if step.role === 'user'}
								{#if step.origin === 'tool' && !step.clean_text && parsed.toolResponses.length > 0 && parsed.domMapCounts.length === 0}
									<!-- Retours d'outils discrets -->
									<div class="flex flex-wrap items-center gap-1.5 px-1">
										{#each parsed.toolResponses as tr}
											<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border border-gray-200/70 dark:border-gray-700/70">
												<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
												Retour : {tr.name}
											</span>
										{/each}
									</div>
								{:else if step.origin === 'human'}
									<!-- Message humain -->
									<div class="rounded-xl p-3 bg-emerald-50/70 dark:bg-emerald-950/25 border border-emerald-200/80 dark:border-emerald-800/40 space-y-1.5">
										<div class="flex items-center justify-between gap-2">
											<div class="flex items-center gap-1.5">
												<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
													Vous
												</span>
												{#if isMultiMember}
													<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-200/70 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
														→ {getParticipantLabel(step.sub_sid, step)}
													</span>
												{/if}
											</div>
											<span class="text-[11px] text-gray-400 font-mono">
												{formatTimestamp(step.updated_at)}
											</span>
										</div>
										<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
											{step.clean_text ?? parsed.visibleTexts.join('\n')}
										</div>
									</div>
								{:else}
									<!-- Directive orchestrateur -->
									<div class="rounded-xl p-3 bg-indigo-50/70 dark:bg-indigo-950/25 border border-indigo-200/80 dark:border-indigo-800/40 space-y-1.5">
										<div class="flex items-center justify-between gap-2">
											<div class="flex items-center gap-1.5">
												<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-700 dark:text-indigo-300">
													Orchestrateur
												</span>
												{#if isMultiMember}
													<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300">
														→ {getParticipantLabel(step.sub_sid, step)}
													</span>
												{/if}
											</div>
											<span class="text-[11px] text-gray-400 font-mono">
												{formatTimestamp(step.updated_at)}
											</span>
										</div>
										{#if step.clean_text || parsed.visibleTexts.length > 0}
											<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
												{step.clean_text ?? parsed.visibleTexts.join('\n')}
											</div>
										{/if}
										{#if parsed.domMapCounts.length > 0}
											<div class="flex flex-wrap gap-1.5">
												{#each parsed.domMapCounts as domCount}
													<span class="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 border border-cyan-500/20 font-mono">
														[Carte DOM : {domCount} éléments]
													</span>
												{/each}
											</div>
										{/if}
										{#if parsed.toolResponses.length > 0}
											<div class="flex flex-wrap gap-1.5 pt-1">
												{#each parsed.toolResponses as tr}
													<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-white/80 dark:bg-gray-900/70 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700">
														Retour : {tr.name}
													</span>
												{/each}
											</div>
										{/if}
									</div>
								{/if}
							{:else}
								<!-- Réponse du sous-agent (model) -->
								<div
									class="rounded-xl p-3 space-y-2 border {step.is_question
										? 'bg-amber-50/60 dark:bg-amber-950/20 border-amber-300 dark:border-amber-700/60'
										: 'bg-gray-50 dark:bg-gray-850 border-gray-200 dark:border-gray-800'}"
								>
									<div class="flex items-center justify-between gap-2 flex-wrap">
										<div class="flex items-center gap-1.5 flex-wrap">
											<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200">
												{getParticipantLabel(step.sub_sid, step)}
											</span>
											{#if stepModelBadge}
												<span class="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border {stepModelBadge.badgeClass}">
													<span>{stepModelBadge.icon}</span>
													<span>{stepModelBadge.label}</span>
												</span>
											{/if}
											{#if step.is_question}
												<span class="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-800 dark:text-amber-300 border border-amber-500/30">
													❓ Question
												</span>
											{/if}
										</div>
										<span class="text-[11px] text-gray-400 font-mono">
											{formatTimestamp(step.updated_at)}
										</span>
									</div>

									{#if step.escalation_target}
										<div class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-purple-500/10 border border-purple-500/25 text-[11px] text-purple-700 dark:text-purple-300 font-medium">
											<span>Transition cognitive →</span>
											{#if escalationBadge}
												<span class="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border {escalationBadge.badgeClass}">
													<span>{escalationBadge.icon}</span>
													<span>{escalationBadge.label}</span>
												</span>
											{:else}
												<span class="font-mono">{step.escalation_target}</span>
											{/if}
										</div>
									{/if}

									{#if parsed.thoughtTexts.length > 0}
										<details class="rounded-lg bg-white/70 dark:bg-gray-900/60 border border-gray-200/70 dark:border-gray-800 text-[11px]">
											<summary class="cursor-pointer select-none px-2.5 py-1.5 text-gray-500 dark:text-gray-400 font-medium hover:text-gray-700 dark:hover:text-gray-200">
												Réflexion interne
											</summary>
											<div class="px-2.5 pb-2 pt-1 text-gray-600 dark:text-gray-300 whitespace-pre-wrap leading-relaxed border-t border-gray-100 dark:border-gray-800">
												{parsed.thoughtTexts.join('\n\n')}
											</div>
										</details>
									{/if}

									{#if parsed.visibleTexts.length > 0}
										<div class="text-xs text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
											{parsed.visibleTexts.join('\n')}
										</div>
									{:else if parsed.fallbackRaw}
										<pre class="font-mono text-[11px] overflow-x-auto p-2 bg-white dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-800">{parsed.fallbackRaw}</pre>
									{/if}

									{#if parsed.domMapCounts.length > 0}
										<div class="flex flex-wrap gap-1.5">
											{#each parsed.domMapCounts as domCount}
												<span class="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 border border-cyan-500/20 font-mono">
													[Carte DOM : {domCount} éléments]
												</span>
											{/each}
										</div>
									{/if}

									{#if parsed.toolCalls.length > 0}
										{@const pendingTools = isStepPendingTools(step)}
										<div class="flex flex-wrap items-center gap-1.5 pt-0.5">
											{#each parsed.toolCalls as tc}
												<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-700 dark:text-purple-300 border border-purple-500/20 font-mono">
													<span class="w-1.5 h-1.5 rounded-full bg-purple-500 {pendingTools ? 'animate-ping' : ''}"></span>
													{tc.name}
												</span>
											{/each}
											{#if pendingTools}
												<span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 animate-pulse font-medium">
													⚙️ Exécution en cours...
												</span>
											{/if}
										</div>
									{/if}
								</div>
							{/if}
						{/each}
					{/if}
				</div>

				{#if !autoFollow}
					<div class="pointer-events-none absolute bottom-3 left-0 right-0 flex justify-center">
						<button
							type="button"
							on:click={resumeAutoFollow}
							class="pointer-events-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-gray-900/90 dark:bg-gray-100/95 text-white dark:text-gray-900 shadow-lg hover:scale-[1.02] transition"
						>
							<span>🔴 Reprendre le suivi temps réel</span>
							{#if pausedNewSteps > 0}
								<span class="px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-red-500 text-white">
									+{pausedNewSteps}
								</span>
							{/if}
						</button>
					</div>
				{/if}
			</div>

			<!-- Bandeau file d'attente FIFO -->
			{#if selectedQueue.length > 0}
				<div class="mx-3 mb-2 rounded-xl border border-amber-300/80 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 space-y-1.5 shrink-0">
					<div class="flex items-center justify-between text-[11px] font-semibold text-amber-800 dark:text-amber-300">
						<span>File d'attente ({selectedQueue.length}) — envoi au prochain tour</span>
					</div>
					<div class="space-y-1 max-h-24 overflow-y-auto">
						{#each selectedQueue as item, idx (item.msg.id ?? `${item.sid}:${idx}`)}
							<div class="text-xs text-amber-900 dark:text-amber-100 bg-white/70 dark:bg-gray-900/50 rounded-lg px-2 py-1 border border-amber-200/70 dark:border-amber-800/40 whitespace-pre-wrap">
								{#if (selectedGroup?.member_sids.length ?? 0) > 1}
									<span class="font-semibold text-[10px] mr-1">[{getParticipantLabel(item.sid)}]</span>
								{/if}
								{item.msg.content}
							</div>
						{/each}
					</div>
				</div>
			{/if}

			<!-- Compositeur interactif -->
			<div class="p-3 border-t border-gray-200 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-850/70 shrink-0 space-y-2">
				{#if selectedGroup && selectedGroup.member_sids.length > 1}
					<div class="flex items-center gap-2 text-[11px]">
						<span class="text-gray-500 dark:text-gray-400 shrink-0">Destinataire :</span>
						<select
							aria-label="Choisir le membre destinataire"
							bind:value={composerTargetSubSid}
							class="flex-1 min-w-0 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-[11px] font-medium text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
						>
							{#each selectedGroup.member_sids as sid (sid)}
								<option value={sid}>{getParticipantLabel(sid)}</option>
							{/each}
						</select>
					</div>
				{/if}

				<div class="flex items-end gap-2">
					<textarea
						bind:value={composerText}
						on:keydown={handleComposerKeydown}
						rows="2"
						disabled={sending}
						placeholder={targetIsRunning
							? 'Envoyer une consigne (mise en file pour le prochain tour)...'
							: 'Envoyer une consigne directe au sous-agent...'}
						class="flex-1 resize-none rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-xs text-gray-800 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-50"
					></textarea>
					<button
						type="button"
						disabled={sending || !composerText.trim()}
						on:click={sendMessage}
						class="px-3 py-2 rounded-xl text-xs font-medium bg-indigo-600 hover:bg-indigo-700 text-white transition disabled:opacity-40 disabled:cursor-not-allowed shrink-0 flex items-center gap-1.5"
					>
						{#if sending}
							<Spinner className="w-3.5 h-3.5" />
						{:else}
							<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
							</svg>
						{/if}
						Envoyer
					</button>
				</div>
				<div class="text-[10px] text-gray-400">
					Entrée pour envoyer · Maj+Entrée pour nouvelle ligne
				</div>
			</div>
		{/if}
	{:else}
		<!-- Onglet Appels d'outils -->
		<div class="flex-1 overflow-y-auto p-4">
			{#if toolCalls.length === 0}
				<div class="flex flex-col items-center justify-center h-48 text-gray-400 text-xs">
					<svg class="w-8 h-8 mb-2 opacity-50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
						<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
					</svg>
					Aucun appel d'outil consigné pour cette session.
				</div>
			{:else}
				<div class="space-y-2">
					{#each toolCalls as call}
						{@const rawArgs = call.args ?? call.args_json}
						<div class="p-3 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-850 font-mono text-xs space-y-1.5">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2">
									<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
									<span class="font-bold text-emerald-600 dark:text-emerald-400">
										{call.function_name}
									</span>
								</div>
								<span class="text-[11px] text-gray-400">{formatTimestamp(call.timestamp)}</span>
							</div>

							{#if call.call_id}
								<div class="text-[10px] text-gray-400 truncate">Réf. : {call.call_id}</div>
							{/if}

							{#if rawArgs !== undefined && rawArgs !== null}
								<div class="p-2 bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-100 dark:border-gray-800 overflow-x-auto text-[11px]">
									<pre>{typeof rawArgs === 'object' ? JSON.stringify(rawArgs, null, 2) : rawArgs}</pre>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>
