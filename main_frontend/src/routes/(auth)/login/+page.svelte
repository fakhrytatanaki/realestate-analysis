<script lang="ts">
  import AuthForm from '$lib/components/AuthForm.svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
</script>

<svelte:head><title>Sign in — SpaceEngine</title></svelte:head>

<AuthForm mode="login" error={form?.error} email={form?.email ?? ''} next={data.next} />
