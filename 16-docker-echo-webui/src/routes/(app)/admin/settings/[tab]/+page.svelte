<script>
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';

	onMount(() => {
		const params = new URLSearchParams($page.url.searchParams);
		params.set('settings', ($page.params.tab ?? 'general').replace(/^admin:/, ''));
		goto(`/?${params.toString()}`, { replaceState: true });
	});
</script>
