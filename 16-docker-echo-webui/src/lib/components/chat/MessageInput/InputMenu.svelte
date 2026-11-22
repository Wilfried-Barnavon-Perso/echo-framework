<script lang="ts">
	import { getContext } from 'svelte';
	import { fly } from 'svelte/transition';

	import { config, user, visibleControls } from '$lib/stores';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentArrowUp from '$lib/components/icons/DocumentArrowUp.svelte';
	import Camera from '$lib/components/icons/Camera.svelte';
	import Clip from '$lib/components/icons/Clip.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import Files from './InputMenu/Files.svelte';

	const i18n: any = getContext('i18n');

	export let files = [];

	export let selectedModels: string[] = [];
	export let fileUploadCapableModels: string[] = [];
	export let visionCapableModels: string[] = [];

	export let screenCaptureHandler: Function;
	export let uploadFilesHandler: Function;
	export let inputFilesHandler: Function;

	export let uploadGoogleDriveHandler: Function;
	export let uploadOneDriveHandler: Function;

	export let onUpload: Function;
	export let onClose: Function;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: Function = () => {};

	export let show = false;
	export let tab = '';
	export let closeOnOutsideClick = true;
	$: if (!show) tab = '';
	// [ECHO-V6-HOOK] Aplatissement du menu + : maintien exclusif de files (suppression Add Context / Webpage / Knowledge / Notes / Chats)
	$: hasInternalList = ['tools', 'files'].includes(tab);
	$: toolApprovalModes = [
		{
			value: 'full',
			label: $i18n.t('Full access'),
			description: $i18n.t('Run tools without asking for approval.')
		},
		{
			value: 'ask',
			label: $i18n.t('Ask for approval'),
			description: $i18n.t('Stop before each tool call until you allow or deny it.')
		},
		...($config?.features?.enable_tool_permissions_auto
			? [
					{
						value: 'auto',
						label: $i18n.t('Auto'),
						description: $i18n.t('settings.chat.toolPermissionsAuto.description')
					}
				]
			: [])
	];

	let fileUploadEnabled = true;
	$: fileUploadEnabled =
		fileUploadCapableModels.length === selectedModels.length &&
		($user?.role === 'admin' || $user?.permissions?.chat?.file_upload);

	$: toolPermissionsEnabled = $config?.features?.enable_tool_permissions ?? false;

	$: if (!fileUploadEnabled && files.some((file) => file.type !== 'text')) {
		files = files.filter((file) => file.type === 'text');
	}

	const detectMobile = () => {
		const userAgent = navigator.userAgent || navigator.vendor || window.opera;
		return /android|iphone|ipad|ipod|windows phone/i.test(userAgent);
	};

	const handleFileChange = (event) => {
		const inputFiles = Array.from(event.target?.files);
		if (inputFiles && inputFiles.length > 0) {
			console.log(inputFiles);
			inputFilesHandler(inputFiles);
		}
	};

	const onSelect = (item) => {
		if (files.find((f) => f.id === item.id)) {
			return;
		}
		files = [
			...files,
			{
				...item,
				status: 'processed'
			}
		];

		show = false;
	};
</script>

<!-- Hidden file input used to open the camera on mobile -->
<input
	id="camera-input"
	type="file"
	accept="image/*"
	capture="environment"
	on:change={handleFileChange}
	class="hidden"
/>

<Dropdown
	bind:show
	{closeOnOutsideClick}
	visualViewportAware
	maxHeight="18rem"
	contentClass="flex flex-col overflow-hidden!"
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content" class="flex min-h-0 flex-col">
		<DropdownMenu
			className="w-70 min-h-0 transition {hasInternalList
				? 'flex flex-col overflow-hidden'
				: 'overflow-y-auto overflow-x-hidden scrollbar-thin'}"
		>
			{#if tab === ''}
				<div in:fly={{ x: -20, duration: 150 }}>
					{#if toolPermissionsEnabled}
						<button
							role="menuitem"
							class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
							on:click={() => {
								tab = 'tool_permissions';
							}}
						>
							<svg
								class="size-3.5 shrink-0"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="1.5"
								stroke-linecap="round"
								stroke-linejoin="round"
							>
								<path d="M12 22C12 22 20 18 20 12V5L12 2L4 5V12C4 18 12 22 12 22Z" />
							</svg>

							<div class="flex items-center w-full justify-between min-w-0">
								<div class="line-clamp-1">
									{$i18n.t('Tool Permissions')}
								</div>

								<div class="flex items-center gap-2 min-w-0">
									<div class="text-xs text-gray-500 truncate">
										{$i18n.t(
											toolApprovalModes.find((mode) => mode.value === toolApprovalMode)?.label ??
												'Full access'
										)}
									</div>
									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</div>
						</button>

						<div class="h-px mx-1 my-1 bg-gray-100 dark:bg-gray-800"></div>
					{/if}

					<slot name="terminal" />

					<Tooltip
						content={fileUploadCapableModels.length !== selectedModels.length
							? $i18n.t('Model(s) do not support file upload')
							: !fileUploadEnabled
								? $i18n.t('You do not have permission to upload files.')
								: ''}
						className="w-full"
					>
						<button
							role="menuitem"
							class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
								? 'opacity-50'
								: ''}"
							type="button"
							on:click={() => {
								if (fileUploadEnabled) {
									uploadFilesHandler();
									show = false;
								}
							}}
						>
							<Clip />

							<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
						</button>
					</Tooltip>

					{#if visionCapableModels.length > 0 && $visibleControls['chat.capture']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								type="button"
								on:click={() => {
									if (fileUploadEnabled) {
										if (!detectMobile()) {
											screenCaptureHandler();
										} else {
											const cameraInputElement = document.getElementById('camera-input');

											if (cameraInputElement) {
												cameraInputElement.click();
											}
										}
										show = false;
									}
								}}
							>
								<Camera />
								<div class=" line-clamp-1">{$i18n.t('Capture')}</div>
							</button>
						</Tooltip>
					{/if}

					{#if $visibleControls['chat.attach_files']}
						<Tooltip
							content={fileUploadCapableModels.length !== selectedModels.length
								? $i18n.t('Model(s) do not support file upload')
								: !fileUploadEnabled
									? $i18n.t('You do not have permission to upload files.')
									: ''}
							className="w-full"
						>
							<button
								role="menuitem"
								type="button"
								class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left {!fileUploadEnabled
									? 'opacity-50'
									: ''}"
								on:click={() => {
									if (fileUploadEnabled) {
										tab = 'files';
									}
								}}
							>
								<DocumentArrowUp />

								<div class="flex items-center w-full justify-between">
									<div class="line-clamp-1">
										{$i18n.t('Attach Files')}
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						</Tooltip>
					{/if}
				</div>
			{:else if tab === 'tool_permissions'}
				<div class="flex min-h-0 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Tool Permissions')}
							</div>
						</div>
					</button>

					<div class="mt-1 space-y-1">
						{#each toolApprovalModes as mode}
							<Tooltip content={$i18n.t(mode.description)} className="w-full">
								<button
									role="menuitemradio"
									aria-checked={toolApprovalMode === mode.value}
									class="flex gap-2 w-full items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer hover:bg-gray-50/40 dark:hover:bg-gray-800/40 rounded-xl text-left"
									on:click={() => {
										toolApprovalMode = mode.value;
										onToolApprovalModeChange(mode.value);
										tab = '';
									}}
								>
									<div class="flex items-center w-full justify-between min-w-0">
										<div class="line-clamp-1">{$i18n.t(mode.label)}</div>
										{#if toolApprovalMode === mode.value}
											<svg
												class="size-3 shrink-0 text-gray-500"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="2.5"
												stroke-linecap="round"
												stroke-linejoin="round"
											>
												<polyline points="20 6 9 17 4 12" />
											</svg>
										{/if}
									</div>
								</button>
							</Tooltip>
						{/each}
					</div>
				</div>
			{:else if tab === 'files'}
				<div class="flex min-h-0 flex-col overflow-hidden" in:fly={{ x: 20, duration: 150 }}>
					<button
						role="menuitem"
						type="button"
						class="flex w-full shrink-0 justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal select-none cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Files')}
							</div>
						</div>
					</button>

					<Files {onSelect} />
				</div>
			{/if}
			<slot name="integrations" />
		</DropdownMenu>
	</div>
</Dropdown>
