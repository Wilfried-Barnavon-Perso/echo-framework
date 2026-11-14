<script>
	import { toast } from 'svelte-sonner';
	import { onMount } from 'svelte';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { config } from '$lib/stores';
	import { getFunctionById } from '$lib/apis/functions';

	import FunctionEditor from '$lib/components/admin/Functions/FunctionEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let func = null;

	onMount(async () => {
		if (!$config?.features?.enable_functions) {
			goto('/admin', { replaceState: true });
			return;
		}

		const id = $page.url.searchParams.get('id');

		if (id) {
			func = await getFunctionById(localStorage.token, id).catch((error) => {
				toast.error(`${error}`);
				goto('/admin/functions');
				return null;
			});
		}
	});
</script>

{#if func}
	<div class="px-[1rem] h-full min-w-0 overflow-x-hidden">
		<FunctionEditor
			edit={true}
			readOnly={true}
			id={func.id}
			name={func.name}
			meta={func.meta}
			content={func.content}
			version_id={func.version_id}
		/>
	</div>
{:else}
	<div class="flex items-center justify-center h-full">
		<div class=" pb-16">
			<Spinner className="size-5" />
		</div>
	</div>
{/if}
