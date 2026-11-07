(() => {
  const form = document.querySelector('#auth-form');
  const message = document.querySelector('#auth-message');
  const submit = document.querySelector('#auth-submit');
  const mode = document.body.dataset.authMode;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  const showMessage = (text) => {
    message.textContent = text;
    message.hidden = !text;
  };

  // Surface a friendly note after logout, without loading the main app shell.
  if (new URLSearchParams(window.location.search).get('logged_out') === '1') {
    showMessage('You have been logged out successfully.');
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    showMessage('');

    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form).entries());
    if (mode === 'register' && values.password !== values.confirm_password) {
      showMessage('The passwords do not match.');
      document.querySelector('#confirm-password').focus();
      return;
    }
    delete values.confirm_password;

    submit.disabled = true;
    submit.textContent = mode === 'login' ? 'Signing in…' : 'Creating account…';
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify(values)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to continue. Please try again.');
      // Load a fresh document so the authenticated layout is rendered only after login.
      window.location.replace('/');
    } catch (error) {
      showMessage(error.message || 'Unable to connect. Check that GamesVault is running.');
      submit.disabled = false;
      submit.textContent = mode === 'login' ? 'Sign in' : 'Create account';
    }
  });
})();
