import { fail, redirect } from '@sveltejs/kit';
import { api, ApiError, safeNext, setSessionCookie } from '$lib/server/api';
import type { Session } from '$lib/types';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = ({ url }) => ({ next: url.searchParams.get('next') ?? '' });

export const actions: Actions = {
  default: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get('email') ?? '').trim();
    const password = String(form.get('password') ?? '');
    const displayName = String(form.get('display_name') ?? '').trim();
    const next = safeNext(String(form.get('next') ?? ''));
    const keep = { email, displayName };

    if (!email || !password) return fail(400, { ...keep, error: 'Enter an email and a password.' });
    if (password.length < 10) {
      return fail(400, { ...keep, error: 'Use a password of at least 10 characters.' });
    }

    let session: Session;
    try {
      session = await api<Session>(event, '/api/v1/auth/register', {
        method: 'POST',
        body: { email, password, display_name: displayName },
        token: null,
        userAgent: event.request.headers.get('user-agent')
      });
    } catch (error) {
      if (error instanceof ApiError) {
        const message =
          error.status === 409
            ? 'An account with this email already exists. Try signing in.'
            : error.status === 403
              ? 'Sign-up is currently closed.'
              : error.detail;
        return fail(error.status >= 500 ? error.status : 400, { ...keep, error: message });
      }
      throw error;
    }
    setSessionCookie(event.cookies, session.token, session.expires_at);
    redirect(303, next);
  }
};
