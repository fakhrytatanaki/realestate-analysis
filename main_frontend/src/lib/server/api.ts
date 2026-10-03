import { env } from '$env/dynamic/private';
import type { Cookies } from '@sveltejs/kit';

/** httpOnly cookie holding the opaque session token issued by the core API. */
export const SESSION_COOKIE = 'se_session';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(detail);
  }
}

type ApiEvent = { fetch: typeof fetch; locals: App.Locals; cookies: Cookies };

const baseUrl = () => (env.API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

/**
 * Call the core API from the SvelteKit server, with the session token as a bearer.
 *
 * Every request goes through here, so the browser never sees the token and the
 * API needs no CORS. A 401 on an authenticated call means the session is gone,
 * so the cookie is cleared too.
 */
export async function api<T>(
  event: ApiEvent,
  path: string,
  init: { method?: string; body?: unknown; token?: string | null; userAgent?: string | null } = {}
): Promise<T> {
  const token = init.token === undefined ? event.locals.token : init.token;
  const headers: Record<string, string> = { accept: 'application/json' };
  if (token) headers.authorization = `Bearer ${token}`;
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  if (init.userAgent) headers['user-agent'] = init.userAgent;

  let response: Response;
  try {
    response = await event.fetch(`${baseUrl()}${path}`, {
      method: init.method ?? 'GET',
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body)
    });
  } catch {
    throw new ApiError(503, 'The SpaceEngine service is unreachable. Please try again shortly.');
  }

  if (response.status === 401 && token) {
    event.cookies.delete(SESSION_COOKIE, { path: '/' });
    event.locals.user = null;
    event.locals.token = null;
  }
  if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    // FastAPI validation errors: a list of {loc, msg}.
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      return String(body.detail[0].msg).replace(/^Value error, /, '');
    }
  } catch {
    // fall through
  }
  return `Request failed (${response.status})`;
}

export function setSessionCookie(cookies: Cookies, token: string, expiresAt: string) {
  cookies.set(SESSION_COOKIE, token, {
    path: '/',
    httpOnly: true,
    sameSite: 'lax',
    // `secure` defaults to true except on http://localhost, which is what we want.
    expires: new Date(expiresAt)
  });
}

/** Only same-site relative paths, so `?next=` cannot become an open redirect. */
export function safeNext(next: string | null | undefined, fallback = '/trends'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
    return fallback;
  }
  return next;
}
