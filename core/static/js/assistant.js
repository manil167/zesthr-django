(() => {
  const root = document.querySelector('[data-assistant]');
  if (!root) return;
  const panel = root.querySelector('.hros-chat-panel');
  const toggle = root.querySelector('.hros-chat-launch');
  const form = root.querySelector('[data-chat-form]');
  const input = root.querySelector('textarea');
  const log = root.querySelector('[role="log"]');
  const status = root.querySelector('[data-chat-status]');
  const badge = root.querySelector('[data-chat-mode]');
  const clear = root.querySelector('[data-chat-clear]');
  let ready = false, busy = false;
  const mode = value => { badge.textContent = value === 'ai' ? 'AI support · OpenAI' : 'App guidance · AI not enabled'; };
  const bubble = (role, text, url) => {
    const message = document.createElement('article');
    message.className = 'hros-chat-message ' + (role === 'user' ? 'from-user' : 'from-assistant');
    const label = document.createElement('strong'); label.textContent = role === 'user' ? 'You' : 'HROS Assistant';
    const body = document.createElement('p');
    // Support lightweight formatting without interpreting model text as HTML.
    const allowedLinks = new Set(['/attendance/today/', '/attendance/corrections/mine/', '/leaves/', '/leaves/balances/', '/me/', '/profile/', '/notifications/', '/whatsapp/', '/password/reset/']);
    const tokens = /\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\(([^)\n]+)\)/g;
    let cursor = 0;
    for (const token of text.matchAll(tokens)) {
      body.append(document.createTextNode(text.slice(cursor,token.index)));
      if (token[1]) {
        const strong = document.createElement('b'); strong.textContent = token[1]; body.append(strong);
      } else if (allowedLinks.has(token[3].trim())) {
        const link = document.createElement('a'); link.href = token[3].trim(); link.textContent = token[2]; body.append(link);
      } else body.append(document.createTextNode(token[2]));
      cursor = token.index + token[0].length;
    }
    body.append(document.createTextNode(text.slice(cursor)));
    message.append(label, body);
    if (url && url.startsWith('/') && !url.startsWith('//') && !url.includes('\\')) {
      const link = document.createElement('a'); link.href = url; link.textContent = 'Open the relevant page'; message.append(link);
    }
    log.append(message); log.scrollTop = log.scrollHeight;
  };
  const welcome = () => bubble('assistant', 'Hello! I can help you understand attendance, leave, payslips and how to use HROS. What would you like help with?');
  const request = async (method, body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(root.dataset.endpoint, {method, credentials:'same-origin', signal:controller.signal,
        headers:{'Content-Type':'application/json', 'X-CSRFToken':form.elements.csrfmiddlewaretoken.value},
        ...(body ? {body:JSON.stringify(body)} : {})});
      if (response.redirected) throw new Error('Your session expired. Please sign in again.');
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The assistant is unavailable. Please refresh the page.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Please try again shortly.');
      return data;
    } finally { clearTimeout(timer); }
  };
  const setBusy = value => {
    busy = value; panel.setAttribute('aria-busy', String(value));
    root.querySelectorAll('button[type="submit"], [data-chat-clear], .hros-chat-suggestions button').forEach(button => button.disabled = value);
    status.textContent = value ? 'Thinking…' : '';
  };
  const load = async () => {
    if (ready) return;
    setBusy(true); let failure = '';
    try {
      const data = await request('GET'); mode(data.mode); log.replaceChildren();
      if (data.messages?.length) data.messages.forEach(message => bubble(message.role, message.content)); else welcome();
      ready = true;
    } catch (error) { failure = error.message; }
    finally { setBusy(false); status.textContent = failure; }
  };
  const close = () => { panel.hidden = true; toggle.setAttribute('aria-expanded','false'); toggle.focus(); };
  toggle.addEventListener('click', async () => {
    if (!panel.hidden) { close(); return; }
    panel.hidden = false; toggle.setAttribute('aria-expanded','true'); input.focus(); await load();
  });
  root.querySelector('[data-chat-close]').addEventListener('click', close);
  root.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) close(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!busy) form.requestSubmit(); }
  });
  root.querySelectorAll('.hros-chat-suggestions button').forEach(button => button.addEventListener('click', () => { input.value = button.textContent; form.requestSubmit(); }));
  form.addEventListener('submit', async event => {
    event.preventDefault(); const question = input.value.trim(); if (busy || !question) return;
    bubble('user',question); input.value = ''; setBusy(true);
    let failure = '';
    try { const data = await request('POST',{question}); mode(data.mode); bubble('assistant',data.answer,data.url); ready = true; }
    catch (error) { failure = error.name === 'AbortError' ? 'The response took too long. Please try again.' : error.message; bubble('assistant',failure); }
    finally { setBusy(false); status.textContent = failure; input.focus(); }
  });
  clear.addEventListener('click', async () => {
    if (busy) return; setBusy(true); let failure = '';
    try { const data = await request('DELETE'); mode(data.mode); log.replaceChildren(); welcome(); ready = true; }
    catch (error) { failure = error.message; }
    finally { setBusy(false); status.textContent = failure; input.focus(); }
  });
})();
