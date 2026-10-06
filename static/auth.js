/* Register / login: live (real-time) validation, Gmail availability check, double-submit guard. */
(() => {
  const form = document.getElementById('auth-form'); if (!form) return;
  const F = window.QBForm, mode = form.dataset.authMode, isReg = mode === 'register';
  const box = document.getElementById('auth-error'), msg = box.querySelector('.form-error-message');
  const f = n => form.elements[n];
  const showErr = t => { msg.textContent = t; box.hidden = false; box.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); };
  const touched = new Set(); let emailTaken = false, emailSeq = 0, emailTimer = null;

  const rules = {
    name: () => { const v = f('name').value.trim(); return !v ? 'Enter your full name.' : !F.NAME.test(v) ? 'Use letters only (2–50 characters).' : ''; },
    email: () => { const v = f('email').value.trim(); return !v ? 'Enter your Gmail address.' : !F.GMAIL.test(v) ? 'Use a Gmail address ending in @gmail.com.' : (isReg && emailTaken) ? 'That Gmail is already registered. Try logging in.' : ''; },
    phone: () => { const o = f('cc').selectedOptions[0], n = +o.dataset.len, v = f('phone').value.replace(/\D/g, '');
      return !v ? 'Enter your mobile number.' : v.length !== n ? `Enter exactly ${n} digits for ${o.dataset.name}.` : (f('cc').value === '+91' && !/^[6-9]/.test(v)) ? 'Indian numbers start with 6, 7, 8 or 9.' : ''; },
    password: () => { const v = f('password').value; return !v ? (isReg ? 'Create a password.' : 'Enter your password.') : (isReg ? F.pwIssue(v) : ''); },
    confirm: () => { const v = f('confirm').value; return !v ? 'Re-enter your password.' : v !== f('password').value ? "Passwords don't match." : ''; },
    terms: () => f('terms').checked ? '' : 'Please accept the Terms & Conditions and Privacy Policy.'
  };
  const fields = Object.keys(rules).filter(k => f(k));
  const okText = { name: 'Looks good.', email: 'Gmail looks good.', phone: 'Number looks valid.', password: 'Strong enough.', confirm: 'Passwords match.' };

  function check(k, force) {
    if (!force && !touched.has(k)) return rules[k]();
    const e = rules[k](); if (k === 'terms') return e;
    if (e) F.hint(f(k), e, 'bad'); else if (k === 'email' && isReg && f(k).dataset.checked !== f(k).value.trim().toLowerCase()) F.hint(f(k), 'Checking…', 'busy');
    else F.hint(f(k), mode === 'login' ? '' : okText[k], mode === 'login' ? '' : 'ok');
    return e;
  }
  fields.forEach(k => {
    const el = f(k);
    el.addEventListener('blur', () => { touched.add(k); check(k, true); });
    el.addEventListener('input', () => {
      if (k === 'email' && isReg) { emailTaken = false; el.dataset.checked = ''; scheduleEmailCheck(); }
      if (k === 'password' && isReg) F.meter(el);
      if (touched.has(k) || (k === 'password' && isReg && el.value)) { touched.add(k); check(k, true); }
      if (k === 'password' && f('confirm') && touched.has('confirm')) check('confirm', true);
      box.hidden = true;
    });
  });
  if (f('cc')) f('cc').addEventListener('change', () => { if (touched.has('phone')) check('phone', true); });

  function scheduleEmailCheck() {
    clearTimeout(emailTimer); const v = f('email').value.trim().toLowerCase();
    if (!F.GMAIL.test(v)) return;
    emailTimer = setTimeout(async () => {
      const seq = ++emailSeq;
      try {
        const r = await fetch('/api/check-email?email=' + encodeURIComponent(v)).then(x => x.json());
        if (seq !== emailSeq) return;                       // a newer keystroke superseded this check
        emailTaken = !r.available; f('email').dataset.checked = v; touched.add('email'); check('email', true);
      } catch { /* offline: server still validates on submit */ }
    }, 450);
  }

  form.addEventListener('submit', e => {
    let first = null, firstMsg = '';
    fields.forEach(k => { touched.add(k); const m = check(k, true); if (m && !first) { first = f(k); firstMsg = m; } });
    if (first) { e.preventDefault(); showErr(firstMsg); first.focus(); return; }
    const b = form.querySelector('.auth-submit'); b.disabled = true; b.textContent = isReg ? 'Creating account…' : 'Logging in…';
  });
  if (isReg && f('password').value) F.meter(f('password'));
  if (isReg && F.GMAIL.test(f('email').value.trim())) scheduleEmailCheck();
})();
