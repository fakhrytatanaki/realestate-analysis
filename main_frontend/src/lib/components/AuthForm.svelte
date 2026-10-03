<script lang="ts">
  import { enhance } from '$app/forms';
  import { ArrowRight, LoaderCircle } from '@lucide/svelte';

  let {
    mode,
    error = undefined,
    email = '',
    displayName = '',
    next = ''
  } = $props<{
    mode: 'login' | 'signup';
    error?: string;
    email?: string;
    displayName?: string;
    next?: string;
  }>();

  let pending = $state(false);
  const signup = $derived(mode === 'signup');
</script>

<div class="card auth-card">
  <h2>{signup ? 'Create your account' : 'Welcome back'}</h2>
  <p class="sub">
    {signup
      ? 'Start exploring historical market trends.'
      : 'Sign in to your SpaceEngine workspace.'}
  </p>

  <form
    method="POST"
    use:enhance={() => {
      pending = true;
      return async ({ update }) => {
        await update({ reset: false });
        pending = false;
      };
    }}
  >
    <input type="hidden" name="next" value={next} />
    {#if signup}
      <label class="field-label" for="display_name">Name <span class="opt">(optional)</span></label>
      <input
        class="input"
        id="display_name"
        name="display_name"
        autocomplete="name"
        value={displayName}
        maxlength="128"
      />
    {/if}
    <label class="field-label" for="email">Email</label>
    <input
      class="input"
      id="email"
      name="email"
      type="email"
      autocomplete="email"
      required
      value={email}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={error ? 'auth-error' : undefined}
    />
    <label class="field-label" for="password">Password</label>
    <input
      class="input"
      id="password"
      name="password"
      type="password"
      autocomplete={signup ? 'new-password' : 'current-password'}
      minlength={signup ? 10 : undefined}
      required
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={signup ? 'password-hint' : error ? 'auth-error' : undefined}
    />
    {#if signup}<p class="hint" id="password-hint">At least 10 characters.</p>{/if}

    {#if error}<p class="error-text" id="auth-error" role="alert">{error}</p>{/if}

    <button class="button button-dark submit" disabled={pending}>
      {signup ? 'Create account' : 'Sign in'}
      {#if pending}<LoaderCircle size={16} class="spin" />{:else}<ArrowRight
          size={16}
          strokeWidth={1.5}
        />{/if}
    </button>
  </form>

  <p class="switch">
    {#if signup}
      Already have an account? <a href="/login{next ? `?next=${encodeURIComponent(next)}` : ''}"
        >Sign in</a
      >
    {:else}
      New to SpaceEngine? <a href="/signup{next ? `?next=${encodeURIComponent(next)}` : ''}"
        >Create an account</a
      >
    {/if}
  </p>
</div>

<style>
  .auth-card {
    padding: 32px;
  }
  h2 {
    font-size: 30px;
    letter-spacing: -0.8px;
  }
  .sub {
    margin: 8px 0 10px;
    font-size: 13px;
    color: var(--muted-soft);
  }
  .opt {
    color: #a4af97;
  }
  .hint {
    margin-top: 6px;
    font-size: 11px;
    color: #98a58a;
  }
  .error-text {
    margin-top: 14px;
  }
  .submit {
    width: 100%;
    justify-content: space-between;
    margin-top: 22px;
  }
  .switch {
    margin-top: 20px;
    font-size: 12px;
    color: var(--muted);
  }
  .switch a {
    color: var(--ink);
    border-bottom: 1px solid #bcc7ad;
    padding-bottom: 2px;
  }
  .switch a:hover {
    color: var(--green);
  }
  :global(.spin) {
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
