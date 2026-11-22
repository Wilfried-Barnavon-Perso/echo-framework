<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	// [ECHO-V6-HOOK] Redirection par défaut du Workspace vers Skills ECHO
	onMount(() => {
		goto('/workspace/skills', { replaceState: true });
	});
</script>
