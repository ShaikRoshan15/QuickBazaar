/* Real-time OTP: send / verify without page reloads (forgot, reset and email-verification pages). */
(() => {
  const F = window.QBForm;
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => ({ http: r.status, ...(await r.json().catch(() => ({ ok: false, message: 'Unexpected server response. Please try again.' }))) }))
    .catch(() => ({ ok: false, http: 0, message: 'Network problem. Check your connection and try again.' }));
  const clock = s => String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  function countdown(seconds, onTick, onEnd) {
    const end = Date.now() + seconds * 1000; let timer;
    const tick = () => { const left = Math.max(0, Math.ceil((end - Date.now()) / 1000)); onTick(left); if (left <= 0) { clearInterval(timer); onEnd && onEnd(); } };
    tick(); timer = setInterval(tick, 500); return () => clearInterval(timer);
  }

  /* ---------- Forgot password: send the OTP live, then continue to the reset page ---------- */
  const forgot = document.getElementById('forgot-form');
  if (forgot) {
    const email = forgot.elements.email, btn = document.getElementById('forgot-submit');
    const box = document.getElementById('auth-error'), msg = box.querySelector('.form-error-message');
    const showErr = t => { msg.textContent = t; box.hidden = false; };
    const validate = () => { const v = email.value.trim(); const e = !v ? 'Enter your Gmail address.' : !F.GMAIL.test(v) ? 'Use a Gmail address ending in @gmail.com.' : ''; F.hint(email, e || 'Gmail looks good.', e ? 'bad' : 'ok'); return !e; };
    let touched = false;
    email.addEventListener('input', () => { box.hidden = true; if (touched) validate(); });
    email.addEventListener('blur', () => { touched = true; if (email.value) validate(); });
    forgot.addEventListener('submit', async e => {
      e.preventDefault(); touched = true; if (!validate()) { showErr('Please enter a valid Gmail address ending in @gmail.com.'); email.focus(); return; }
      const v = email.value.trim().toLowerCase(); btn.disabled = true; btn.textContent = 'Sending OTP…'; box.hidden = true;
      const r = await post('/api/otp/send', { email: v, purpose: 'reset' });
      if (r.ok || r.code === 'cooldown') { btn.textContent = 'OTP sent ✓'; location.href = '/reset-password?email=' + encodeURIComponent(v); return; }
      btn.disabled = false; btn.textContent = 'Send OTP'; showErr(r.message);
    });
    return;
  }

  /* ---------- OTP entry (verify email + reset password) ---------- */
  const form = document.getElementById('otp-form') || document.getElementById('reset-form'); if (!form) return;
  const purpose = form.dataset.purpose, email = form.dataset.email, isReset = purpose === 'reset';
  const wrap = document.getElementById('otp-boxes'), status = document.getElementById('otp-status');
  const errBox = document.getElementById('auth-error'), errMsg = errBox.querySelector('.form-error-message');
  const resend = document.getElementById('otp-resend'), resendClock = document.getElementById('resend-clock');
  const expiryRow = document.getElementById('otp-expiry'), expiryClock = document.getElementById('otp-clock');
  const submit = document.getElementById('otp-submit');
  const pwSection = document.getElementById('pw-section');
  let busy = false, verified = false, stopExpiry = null, stopResend = null;

  const inputs = Array.from({ length: 6 }, (_, i) => {
    const el = document.createElement('input');
    el.type = 'text'; el.inputMode = 'numeric'; el.maxLength = 1; el.autocomplete = i === 0 ? 'one-time-code' : 'off';
    el.setAttribute('aria-label', 'Digit ' + (i + 1)); el.className = 'otp-box'; wrap.appendChild(el); return el;
  });
  const code = () => inputs.map(i => i.value).join('');
  const say = (t, kind) => { status.textContent = t || ''; status.className = 'otp-status' + (kind ? ' ' + kind : ''); };
  const setErr = t => { errMsg.textContent = t || ''; errBox.hidden = !t; };
  const clearBoxes = () => { inputs.forEach(i => { i.value = ''; i.classList.remove('bad'); }); inputs[0].focus(); };

  function startResend(secs) {
    stopResend && stopResend(); resend.disabled = true;
    stopResend = countdown(secs, l => resendClock.textContent = l > 0 ? ` (${l}s)` : '', () => { resend.disabled = false; resendClock.textContent = ''; });
  }
  function startExpiry(secs) {
    stopExpiry && stopExpiry(); if (secs <= 0) { expiryRow.hidden = true; return; } expiryRow.hidden = false; expiryRow.classList.remove('gone');
    stopExpiry = countdown(secs, l => { expiryClock.textContent = clock(l); }, () => { if (!verified) { expiryRow.classList.add('gone'); expiryRow.textContent = 'This OTP has expired. Tap “Send a new OTP”.'; } });
  }

  function lockPassword(lock) {
    if (!pwSection) return;
    pwSection.classList.toggle('locked', lock);
    pwSection.querySelectorAll('input,button').forEach(el => { el.disabled = lock; });
  }

  async function verify() {
    if (busy || verified) return; const c = code();
    if (c.length !== 6) { say('Enter all 6 digits.', 'bad'); return; }
    busy = true; setErr(''); say('Verifying…', 'busy'); inputs.forEach(i => i.disabled = true); if (submit) submit.disabled = true;
    const r = await post('/api/otp/verify', { email, purpose, code: c });
    busy = false;
    if (r.ok) {
      verified = true; inputs.forEach(i => { i.disabled = true; i.classList.add('good'); }); say('✓ ' + r.message, 'ok');
      stopExpiry && stopExpiry(); expiryRow.hidden = true; resend.closest('p').hidden = true;
      if (isReset) { lockPassword(false); const pw = form.elements.password; pw && pw.focus(); }
      else if (r.redirect) { if (submit) submit.textContent = 'Verified ✓'; setTimeout(() => location.href = r.redirect, 900); }
      return;
    }
    inputs.forEach(i => { i.disabled = false; i.classList.add('bad'); }); if (submit) submit.disabled = false;
    wrap.classList.remove('shake'); void wrap.offsetWidth; wrap.classList.add('shake');
    say(r.message, 'bad'); setTimeout(clearBoxes, 450);
  }

  inputs.forEach((el, i) => {
    el.addEventListener('input', () => {
      el.value = el.value.replace(/\D/g, '').slice(-1); inputs.forEach(x => x.classList.remove('bad')); say('');
      if (el.value && i < 5) inputs[i + 1].focus(); if (code().length === 6) verify();
    });
    el.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !el.value && i > 0) { inputs[i - 1].focus(); inputs[i - 1].value = ''; }
      else if (e.key === 'ArrowLeft' && i > 0) inputs[i - 1].focus(); else if (e.key === 'ArrowRight' && i < 5) inputs[i + 1].focus();
    });
    el.addEventListener('focus', () => el.select());
    el.addEventListener('paste', e => {
      const d = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6); if (!d) return; e.preventDefault();
      d.split('').forEach((ch, k) => inputs[k].value = ch); inputs[Math.min(d.length, 5)].focus(); if (d.length === 6) verify();
    });
  });

  resend.addEventListener('click', async () => {
    if (verified) return; resend.disabled = true; setErr(''); say('Sending a new OTP…', 'busy');
    const r = await post('/api/otp/send', { email, purpose });
    if (r.ok) { say(r.message || 'A new OTP is on its way.', 'ok'); clearBoxes(); startResend(r.retry_after || 30); startExpiry(r.ttl || 600); }
    else if (r.code === 'cooldown') { say(r.message, 'bad'); startResend(r.retry_after || 30); }
    else { say(''); setErr(r.message); resend.disabled = false; }
  });

  form.addEventListener('submit', e => {
    if (verified && !isReset) { e.preventDefault(); return; }
    if (isReset) {
      if (!verified) { e.preventDefault(); say('Enter and verify the OTP first.', 'bad'); inputs[0].focus(); return; }
      const pw = form.elements.password, cf = form.elements.confirm;
      const issue = F.pwIssue(pw.value), match = pw.value === cf.value;
      F.hint(pw, issue || 'Strong enough.', issue ? 'bad' : 'ok'); F.hint(cf, match ? 'Passwords match.' : "Passwords don't match.", match ? 'ok' : 'bad');
      if (issue || !match) { e.preventDefault(); setErr(issue || "Passwords don't match."); return; }
      const b = document.getElementById('reset-submit'); b.disabled = true; b.textContent = 'Resetting…'; return;
    }
    e.preventDefault(); verify();      // verify page: the button just triggers the same live check
  });

  if (isReset) {
    const pw = form.elements.password, cf = form.elements.confirm;
    pw.addEventListener('input', () => { F.meter(pw); const i = F.pwIssue(pw.value); F.hint(pw, pw.value ? (i || 'Strong enough.') : '', pw.value ? (i ? 'bad' : 'ok') : ''); setErr(''); if (cf.value) cf.dispatchEvent(new Event('input')); });
    cf.addEventListener('input', () => { if (!cf.value) { F.hint(cf, '', ''); return; } const m = cf.value === pw.value; F.hint(cf, m ? 'Passwords match.' : "Passwords don't match.", m ? 'ok' : 'bad'); setErr(''); });
  }

  // initial state (survives a page refresh because the server tells us the cooldown / expiry that's left)
  const wait = +form.dataset.wait || 0, expires = +form.dataset.expires || 0;
  if (isReset && form.dataset.verified === '1') {
    verified = true; inputs.forEach(i => { i.disabled = true; i.classList.add('good'); }); say('✓ OTP verified. Choose a new password.', 'ok'); resend.closest('p').hidden = true; lockPassword(false);
  } else {
    lockPassword(isReset); if (wait > 0) startResend(wait); else resend.disabled = false;
    if (expires > 0) startExpiry(expires); else if (!isReset) say('No active OTP. Tap “Send a new OTP” to get one.', 'bad');
    inputs[0].focus();
  }
})();
