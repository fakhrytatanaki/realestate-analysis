import { redirect } from '@sveltejs/kit';
import { api, SESSION_COOKIE } from '$lib/server/api';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = () => redirect(303, '/');

export const actions: Actions = {
  default: async (event) => {
    if (event.locals.token) {
      // Best effort: the cookie goes regardless, so a dead API cannot trap a user signed in.
      await api(event, '/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined);
    }
    event.cookies.delete(SESSION_COOKIE, { path: '/' });
    redirect(303, '/login');
  }
};
