import {createAuthClient} from '@neon/auth';
const form = document.querySelector('[data-neon-signup]');
if (form) {
  const auth = createAuthClient(window.location.origin + '/auth/provider');
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  form.append(status);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const values = new FormData(form);
    if (values.get('password1') !== values.get('password2')) {
      status.textContent = 'Passwords do not match.'; return;
    }
    const button = form.querySelector('button[type=submit]');
    button.disabled = true; status.textContent = 'Creating account…';
    try {
      const {data, error} = await auth.signUp.email({
        email: values.get('email'), password: values.get('password1'),
        name: `${values.get('first_name')} ${values.get('last_name')}`,
      }, {headers:{'X-CSRFToken':values.get('csrfmiddlewaretoken')}});
      if (error) throw new Error(error.message || 'Account could not be created.');
      if (!data?.user?.id) throw new Error('No user was returned. Please try again.');
      window.location.assign('/verify-email/');
    } catch (error) { status.textContent = error.message || 'Signup is temporarily unavailable.'; }
    finally { button.disabled = false; }
  });
}
