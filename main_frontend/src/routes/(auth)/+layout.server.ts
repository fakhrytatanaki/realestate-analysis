import { redirect } from '@sveltejs/kit';
import { safeNext } from '$lib/server/api';
import type { LayoutServerLoad } from './$types';

/** Signed-in visitors have no business on the sign-in pages. */
export const load: LayoutServerLoad = ({ locals, url }) => {
  if (locals.user) redirect(303, safeNext(url.searchParams.get('next')));
};
