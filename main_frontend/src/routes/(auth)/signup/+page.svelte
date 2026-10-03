<script lang="ts">
  import AuthForm from '$lib/components/AuthForm.svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
</script>

<svelte:head><title>Create account — SpaceEngine</title></svelte:head>

<AuthForm
  mode="signup"
  error={form?.error}
  email={form?.email ?? ''}
  displayName={form?.displayName ?? ''}
  next={data.next}
/>
