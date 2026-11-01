// Uses a real account. Supply test credentials securely; no credentials/tokens are logged.
import {existsSync} from 'node:fs';
import {createAuthClient} from '@neon/auth';
import {BetterAuthVanillaAdapter} from '@neon/auth/vanilla/adapters';
import {setGlobalDispatcher, EnvHttpProxyAgent} from 'undici';
for (const file of ['.env.local', '.env']) {
  if (existsSync(file)) process.loadEnvFile(file);
}
setGlobalDispatcher(new EnvHttpProxyAgent());
const base = process.env.NEON_AUTH_BASE_URL;
const email = process.env.NEON_TEST_EMAIL;
const password = process.env.NEON_TEST_PASSWORD;
if (!base || !email || !password) {
  console.error('BLOCKED: set NEON_AUTH_BASE_URL, NEON_TEST_EMAIL and NEON_TEST_PASSWORD securely before the real-user test.');
  process.exit(1);
}
const jar = new Map();
const proxyFetch = async (url, options) => {
  const headers = new Headers(options?.headers);
  headers.set('Origin', process.env.NEON_AUTH_APP_ORIGIN || 'http://localhost:8000');
  if (jar.size) headers.set('Cookie', [...jar].map(([k,v]) => `${k}=${v}`).join('; '));
  const response = await fetch(url, {...options, headers, redirect:'error'});
  for (const cookie of response.headers.getSetCookie()) {
    const pair = cookie.split(';', 1)[0]; const split = pair.indexOf('=');
    if (split > 0) jar.set(pair.slice(0, split), pair.slice(split + 1));
  }
  return response;
};
const auth = createAuthClient(base, {adapter:BetterAuthVanillaAdapter({fetchOptions:{customFetchImpl:proxyFetch}})});
try {
  if (process.env.NEON_TEST_EXISTING_USER !== '1') {
    const signup = await auth.signUp.email({email, password, name:'HROS integration test'});
    if (signup.error || !signup.data?.user?.id) throw new Error('signup');
    console.log('PASS: real Neon Auth signup returned a user ID (omitted).');
  }
  const signin = await auth.signIn.email({email, password});
  if (signin.error) {
    console.error('BLOCKED: sign-in failed. Verify the test user’s email and credentials, then rerun with NEON_TEST_EXISTING_USER=1.');
    process.exitCode = 1;
  } else {
    const current = await auth.getSession();
    if (current.error || !current.data?.user?.id || current.data.user.email.toLowerCase() !== email.toLowerCase()) throw new Error('current user');
    console.log('PASS: SDK current-user session matches the real test account (identity omitted).');
    const signout = await auth.signOut();
    if (signout.error) throw new Error('signout');
    const after = await auth.getSession({fetchOptions:{headers:{Cookie:[...jar].map(([k,v]) => `${k}=${v}`).join('; ')}}});
    if (after.data?.user) throw new Error('session revocation');
    console.log('PASS: signed out and current-user session is absent.');
  }
} catch {
  console.error('FAIL: Neon Auth SDK signup/session test did not complete. Check network access, Auth configuration and verified test credentials. No secret output was emitted.');
  process.exitCode = 1;
}
