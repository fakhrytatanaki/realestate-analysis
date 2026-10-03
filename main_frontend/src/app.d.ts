// See https://svelte.dev/docs/kit/types#app.d.ts
import type { User } from '$lib/types';

declare global {
  namespace App {
    interface Locals {
      user: User | null;
      /** Raw session token from the httpOnly cookie; server-side only. */
      token: string | null;
    }
    interface PageData {
      user: User | null;
    }
  }
}

export {};
