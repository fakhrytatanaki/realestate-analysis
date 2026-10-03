import { fail } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { appendFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import type { Actions } from './$types';

const roles = new Set(['agent', 'researcher', 'investor', 'developer', 'other']);

export const actions: Actions = {
  join: async ({ request }) => {
    const data = await request.formData();
    const email = String(data.get('email') ?? '')
      .trim()
      .toLowerCase();
    const role = String(data.get('role') ?? '');

    if (data.get('website')) return { success: true };
    if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return fail(400, { error: 'Please enter a valid email address.', email });
    }
    if (!roles.has(role))
      return fail(400, { error: 'Please select the role that best describes you.', email });
    if (data.get('consent') !== 'yes')
      return fail(400, {
        error: 'Please confirm you’d like to receive product and launch updates.',
        email
      });

    try {
      const file = resolve(env.WAITLIST_FILE || '.data/waitlist.jsonl');
      await mkdir(dirname(file), { recursive: true, mode: 0o700 });
      await appendFile(
        file,
        JSON.stringify({
          email,
          role,
          consent: 'product-and-launch-updates',
          createdAt: new Date().toISOString()
        }) + '\n',
        { encoding: 'utf8', mode: 0o600 }
      );
      return { success: true };
    } catch {
      return fail(503, {
        error: 'We couldn’t save your request just now. Please try again in a moment.',
        email
      });
    }
  }
};
