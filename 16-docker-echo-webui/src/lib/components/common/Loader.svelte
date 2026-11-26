<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';
	const dispatch = createEventDispatcher();

	export let pollIntervalMs: number | null = 100;

	let loaderElement: HTMLElement;

	let observer;
	let intervalId;

	onMount(() => {
		observer = new IntersectionObserver(
			(entries, observer) => {
				entries.forEach((entry) => {
					if (entry.isIntersecting) {
						dispatch('visible');
						if (pollIntervalMs !== null && pollIntervalMs > 0) {
							clearInterval(intervalId);
							intervalId = setInterval(() => {
								dispatch('visible');
							}, pollIntervalMs);
						}
						// observer.unobserve(loaderElement); // Stop observing until content is loaded
					} else {
						clearInterval(intervalId);
					}
				});
			},
			{
				root: null, // viewport
				rootMargin: '0px',
				threshold: 0.1 // When 10% of the loader is visible
			}
		);

		observer.observe(loaderElement);
	});

	onDestroy(() => {
		if (observer) {
			observer.disconnect();
		}

		if (intervalId) {
			clearInterval(intervalId);
		}
	});
</script>

<div bind:this={loaderElement}>
	<slot />
</div>
