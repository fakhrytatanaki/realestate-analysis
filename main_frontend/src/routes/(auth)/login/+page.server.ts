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
    const next = safeNext(String(form.get('next') ?? ''));

    if (!email || !password) return fail(400, { email, error: 'Enter your email and password.' });

    let session: Session;
    try {
      session = await api<Session>(event, '/api/v1/auth/login', {
        method: 'POST',
        body: { email, password },
        token: null,
        userAgent: event.request.headers.get('user-agent')
      });
    } catch (error) {
      if (error instanceof ApiError) {
        const message =
          error.status === 401 ? 'That email and password do not match.' : error.detail;
        return fail(error.status === 401 ? 400 : error.status, { email, error: message });
      }
      throw error;
    }
    setSessionCookie(event.cookies, session.token, session.expires_at);
    redirect(303, next);
  }
};
