import type { Handle } from '@sveltejs/kit';
import { api, ApiError, SESSION_COOKIE } from '$lib/server/api';
import type { User } from '$lib/types';

/** Resolve the signed-in user once per request from the session cookie. */
export const handle: Handle = async ({ event, resolve }) => {
  event.locals.user = null;
  event.locals.token = event.cookies.get(SESSION_COOKIE) ?? null;

  if (event.locals.token) {
    try {
      event.locals.user = await api<User>(event, '/api/v1/auth/me');
    } catch (error) {
      // 401 already cleared the cookie in api(); an unreachable API just leaves
      // the visitor signed out for this request without discarding the session.
      if (!(error instanceof ApiError)) throw error;
    }
  }
  return resolve(event);
};
