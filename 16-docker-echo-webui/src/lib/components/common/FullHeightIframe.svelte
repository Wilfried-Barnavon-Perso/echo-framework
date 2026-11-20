<script context="module" lang="ts">
	// contentWindows of embeds rendered here; Chat.svelte trusts prompt messages from these
	const embedWindows = new Set<Window>();

	export const isEmbedWindow = (source: unknown): boolean => embedWindows.has(source as Window);
</script>

<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { chatId, config } from '$lib/stores';
	import { getEchoToken } from '$lib/apis/echo';
	import { injectCsp } from '$lib/utils/csp';

	// Props
	export let src: string | null = null; // URL or raw HTML (auto-detected)
	export let title = 'Embedded Content';
	export let initialHeight: number | null = null; // initial height in px, null = auto

	export let iframeClassName = 'w-full rounded-2xl';

	export let args = null;

	export let allowScripts = true;
	export let allowForms = false;

	export let allowSameOrigin = false; // set to true only when you trust the content
	export let allowPopups = false;
	export let allowDownloads = true;

	export let referrerPolicy: HTMLIFrameElement['referrerPolicy'] =
		'strict-origin-when-cross-origin';
	export let allowFullscreen = true;

	export let payload = null; // payload to send into the iframe on request

	let iframe: HTMLIFrameElement | null = null;
	let iframeSrc: string | null = null;
	let iframeDoc: string | null = null;
	let registeredWindow: Window | null = null;
	let activeEmbedChatId: string | null = null;
	let loadSeq = 0;

	// Derived: build sandbox attribute from flags
	$: sandbox =
		[
			allowScripts && 'allow-scripts',
			allowForms && 'allow-forms',
			allowSameOrigin && 'allow-same-origin',
			allowPopups && 'allow-popups',
			allowDownloads && 'allow-downloads'
		]
			.filter(Boolean)
			.join(' ') || undefined;

	// Detect URL vs raw HTML and prep src/srcdoc
	$: isUrl = typeof src === 'string' && /^((https?:)?\/\/|\/[^/])/i.test(src);
	$: if (src) {
		setIframeSrc();
	}

	const setIframeSrc = async () => {
		const seq = ++loadSeq;
		await tick();
		if (seq !== loadSeq) return;

		if (typeof src === 'string' && src.startsWith('echo-embed://')) {
			const rawRest = src.slice('echo-embed://'.length);
			const qIdx = rawRest.indexOf('?');
			const embedId = (qIdx >= 0 ? rawRest.slice(0, qIdx) : rawRest).replace(/\/+$/, '');
			const queryChatId =
				qIdx >= 0 ? new URLSearchParams(rawRest.slice(qIdx + 1)).get('chat_id') || '' : '';
			const effectiveChatId = $chatId || queryChatId;
			activeEmbedChatId = effectiveChatId || null;

			if (!effectiveChatId || !/^[a-f0-9]{16,64}$/i.test(embedId)) {
				iframeDoc = null;
				iframeSrc = null;
				return;
			}

			try {
				const token = getEchoToken();
				const resp = await fetch(
					`/echo/v1/embeds/${encodeURIComponent(effectiveChatId)}/${encodeURIComponent(embedId)}`,
					{
						headers: token ? { Authorization: `Bearer ${token}` } : {},
						credentials: 'include'
					}
				);
				if (seq !== loadSeq) return;
				if (!resp.ok) {
					iframeDoc = null;
					iframeSrc = null;
					return;
				}
				const htmlText = await resp.text();
				if (seq !== loadSeq) return;
				iframeDoc = await processHtmlForDeps(htmlText);
				if (seq !== loadSeq) return;
				iframeSrc = null;
			} catch (err) {
				console.error('Failed to load echo-embed document:', err);
				if (seq === loadSeq) {
					iframeDoc = null;
					iframeSrc = null;
				}
			}
			return;
		}

		activeEmbedChatId = null;
		if (isUrl) {
			iframeSrc = src as string;
			iframeDoc = null;
		} else {
			iframeDoc = await processHtmlForDeps(src as string);
			if (seq !== loadSeq) return;
			iframeSrc = null;
		}
	};

	// Alpine directives detection
	const alpineDirectives = [
		'x-data',
		'x-init',
		'x-show',
		'x-bind',
		'x-on',
		'x-text',
		'x-html',
		'x-model',
		'x-modelable',
		'x-ref',
		'x-for',
		'x-if',
		'x-effect',
		'x-transition',
		'x-cloak',
		'x-ignore',
		'x-teleport',
		'x-id'
	];

	async function processHtmlForDeps(html: string): Promise<string> {
		if (!allowSameOrigin) return html;

		const scriptTags: string[] = [];

		// Alpine.js detection & injection
		const hasAlpineDirectives = alpineDirectives.some((dir) => html.includes(dir));
		if (hasAlpineDirectives) {
			try {
				const { default: alpineCode } = await import('alpinejs/dist/cdn.min.js?raw');
				const alpineBlob = new Blob([alpineCode], { type: 'text/javascript' });
				const alpineUrl = URL.createObjectURL(alpineBlob);
				const alpineTag = `<script src="${alpineUrl}" defer><\/script>`;
				scriptTags.push(alpineTag);
			} catch (error) {
				console.error('Error processing Alpine for iframe:', error);
			}
		}

		// Chart.js detection & injection
		const chartJsDirectives = ['new Chart(', 'Chart.'];
		const hasChartJsDirectives = chartJsDirectives.some((dir) => html.includes(dir));
		if (hasChartJsDirectives) {
			try {
				// import chartUrl from 'chart.js/auto?url';
				const { default: Chart } = await import('chart.js/auto');
				(window as any).Chart = Chart;

				const chartTag = `<script>
window.Chart = parent.Chart; // Chart previously assigned on parent
<\/script>`;
				scriptTags.push(chartTag);
			} catch (error) {
				console.error('Error processing Chart.js for iframe:', error);
			}
		}

		// If nothing to inject, return original HTML
		if (scriptTags.length === 0) return html;

		const tags = scriptTags.join('\n');

		// Prefer injecting into <head>, then before </body>, otherwise prepend
		if (html.includes('</head>')) {
			return html.replace('</head>', `${tags}\n</head>`);
		}
		if (html.includes('</body>')) {
			return html.replace('</body>', `${tags}\n</body>`);
		}
		return `${tags}\n${html}`;
	}

	// Try to measure same-origin content safely
	function resizeSameOrigin() {
		if (!iframe) return;
		try {
			const doc = iframe.contentDocument || iframe.contentWindow?.document;
			console.log('iframe doc:', doc);
			if (!doc) return;
			const h = Math.max(doc.documentElement?.scrollHeight ?? 0, doc.body?.scrollHeight ?? 0);
			if (h > 0) iframe.style.height = h + 20 + 'px';
		} catch {
			// Cross-origin → rely on postMessage from inside the iframe
		}
	}

	async function handleFetchAsset(data: { asset?: unknown; reqId?: unknown }) {
		const reqId = data?.reqId ?? null;
		const asset = data?.asset;
		const cid = activeEmbedChatId || $chatId || '';
		if (
			typeof asset !== 'string' ||
			!/^[a-f0-9]{16,64}\.[a-z0-9]{2,5}$/i.test(asset) ||
			!cid
		) {
			iframe?.contentWindow?.postMessage({ type: 'echo:asset-error', reqId }, '*');
			return;
		}

		try {
			const token = getEchoToken();
			const resp = await fetch(
				`/echo/v1/embeds/${encodeURIComponent(cid)}/assets/${encodeURIComponent(asset)}`,
				{
					headers: token ? { Authorization: `Bearer ${token}` } : {},
					credentials: 'include'
				}
			);
			if (!resp.ok) {
				iframe?.contentWindow?.postMessage({ type: 'echo:asset-error', reqId }, '*');
				return;
			}
			const blob = await resp.blob();
			iframe?.contentWindow?.postMessage({ type: 'echo:asset-blob', reqId, blob }, '*');
		} catch {
			iframe?.contentWindow?.postMessage({ type: 'echo:asset-error', reqId }, '*');
		}
	}

	function onMessage(e: MessageEvent) {
		if (!iframe || e.source !== iframe.contentWindow) return;

		const data = e.data || {};
		if (data?.type === 'iframe:height' && typeof data.height === 'number') {
			iframe.style.height = Math.max(0, data.height) + 'px';
		}

		// Pong message for testing connectivity
		if (data?.type === 'pong') {
			console.log('Received pong from iframe:', data);

			// Optional: reply back
			iframe.contentWindow?.postMessage({ type: 'pong:ack' }, '*');
		}

		// Send payload data if requested
		if (data?.type === 'payload') {
			iframe.contentWindow?.postMessage(
				{ type: 'payload', requestId: data?.requestId ?? null, payload: payload },
				'*'
			);
		}

		// Fetch session-scoped binary embed asset and relay as Blob
		if (data?.type === 'echo:fetch-asset') {
			void handleFetchAsset(data);
		}
	}

	// When the iframe loads, try same-origin resize (cross-origin will noop)
	const onLoad = async () => {
		requestAnimationFrame(resizeSameOrigin);

		if (iframe?.contentWindow && iframe.contentWindow !== registeredWindow) {
			if (registeredWindow) {
				embedWindows.delete(registeredWindow);
			}
			registeredWindow = iframe.contentWindow;
			embedWindows.add(registeredWindow);
		}

		// if arguments are provided, inject them into the iframe window
		if (args && iframe?.contentWindow) {
			try {
				(iframe.contentWindow as any).args = args;
			} catch {
				// Cross-origin → args cannot be injected
			}
		}
	};

	// Ensure event listener bound only while component lives
	onMount(() => {
		window.addEventListener('message', onMessage);
	});

	onDestroy(() => {
		window.removeEventListener('message', onMessage);
		if (registeredWindow) {
			embedWindows.delete(registeredWindow);
		}
	});
</script>

{#if iframeDoc}
	<iframe
		bind:this={iframe}
		srcdoc={injectCsp(iframeDoc, $config?.ui?.iframe_csp ?? '')}
		{title}
		class={iframeClassName}
		style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
		width="100%"
		frameborder="0"
		{sandbox}
		{allowFullscreen}
		on:load={onLoad}
	/>
{:else if iframeSrc}
	<iframe
		bind:this={iframe}
		src={iframeSrc}
		{title}
		class={iframeClassName}
		style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
		width="100%"
		frameborder="0"
		{sandbox}
		referrerpolicy={referrerPolicy}
		{allowFullscreen}
		on:load={onLoad}
	/>
{/if}
