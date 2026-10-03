<script lang="ts" generics="T extends string">
  let {
    label,
    options,
    value,
    onchange
  }: {
    label: string;
    options: readonly { value: T; label: string }[];
    /** null when no option applies, e.g. a custom date range. */
    value: T | null;
    onchange: (value: T) => void;
  } = $props();
</script>

<div class="segmented" role="group" aria-label={label}>
  {#each options as option (option.value)}
    <button
      type="button"
      aria-pressed={option.value === value}
      onclick={() => option.value !== value && onchange(option.value)}>{option.label}</button
    >
  {/each}
</div>

<style>
  .segmented {
    display: inline-flex;
    padding: 3px;
    gap: 2px;
    border: 1px solid var(--field-border);
    border-radius: 6px;
    background: #fff;
  }
  button {
    border: 0;
    background: transparent;
    font-size: 12px;
    padding: 7px 12px;
    border-radius: 4px;
    color: var(--muted);
    white-space: nowrap;
  }
  button:hover {
    color: var(--ink);
  }
  button[aria-pressed='true'] {
    background: var(--ink-strong);
    color: #f7f9f0;
  }
</style>
