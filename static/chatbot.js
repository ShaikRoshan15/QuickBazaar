(() => {
  const $ = s => document.querySelector(s);
  const root = document.documentElement, sp = $('#splash');
  // Splash + logo animation: plays once per browser session when the site is opened from a link.
  // It ALWAYS lasts exactly SPLASH_MS (6 seconds), no matter how fast or slow the page loads, then the site opens and the
  // header logo plays its entrance animation. A CSS animation on #splash (style.css, "qbSplashAuto") is a JS-independent
  // fail-safe that hides the splash at the same moment even if this script were delayed or failed.
  const SPLASH_MS = 6000;
  const first = !root.classList.contains('no-splash');
  try { sessionStorage.setItem('qb_seen', '1'); } catch {}
  const pageReady = () => root.classList.add('page-loaded');          // header logo plays its entrance animation now
  if (sp && first) {
    const msgs = ['Opening the bazaar\u2026', 'Finding fresh deals near you\u2026', 'Checking sellers are verified\u2026', 'Polishing the shelves\u2026', 'Almost ready!'];
    const tag = $('#sp-tag'); let k = 0, done = false; if (tag) tag.textContent = msgs[0];
    const t0 = window.__qbSplashStart || Date.now();                   // set by an inline script right after the splash markup
    const iv = setInterval(() => { k = Math.min(k + 1, msgs.length - 1); if (tag) tag.textContent = msgs[k]; }, SPLASH_MS / msgs.length);
    const finish = () => {
      if (done) return; done = true; clearInterval(iv); sp.classList.add('hide');
      setTimeout(() => { sp.remove(); pageReady(); }, 450);
    };
    const elapsed = () => Date.now() - t0;
    setTimeout(finish, Math.max(0, SPLASH_MS - elapsed()));
    // background tabs throttle timers: finish the moment the tab is visible again if the 6 s already passed
    document.addEventListener('visibilitychange', () => { if (!document.hidden && elapsed() >= SPLASH_MS) finish(); });
  } else { if (sp) sp.remove(); if (document.readyState === 'complete') pageReady(); else window.addEventListener('load', pageReady, { once: true }); setTimeout(pageReady, 4000); }
  document.addEventListener('click', e => { const b = e.target.closest('[data-all]'); if (b) document.querySelectorAll('.lcard').forEach(d => d.open = b.dataset.all === 'open'); });

  // mobile menu
  const nt = $('#nav-toggle'), nav = $('#main-nav');
  if (nt && nav) {
    const set = open => { nav.classList.toggle('open', open); nt.setAttribute('aria-expanded', open); nt.setAttribute('aria-label', open ? 'Close menu' : 'Open menu'); };
    nt.addEventListener('click', e => { e.stopPropagation(); set(!nav.classList.contains('open')); });
    document.addEventListener('click', e => { if (!e.target.closest('#main-nav')) set(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') set(false); });
  }
  // profile photo preview
  const pf = document.querySelector('input[name=profile_pic]');
  if (pf) pf.addEventListener('change', () => { const f = pf.files[0], w = document.querySelector('.profile-pic-wrap'); if (!f || !w) return;
    let img = w.querySelector('img.profile-pic'); if (!img) { img = new Image(); img.className = 'profile-pic'; w.replaceChild(img, w.querySelector('.profile-pic.placeholder')); } img.src = URL.createObjectURL(f); });

  const log = $('#bot-log'), box = $('#bot');
  const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const add = (html, me) => { const d = document.createElement('div'); d.className = 'bm' + (me ? ' me' : ''); d.innerHTML = html; log.appendChild(d); log.scrollTop = 1e6; return d; };
  const chips = list => {
    if (!list || !list.length) return;
    const w = document.createElement('div'); w.className = 'chips-b';
    list.forEach(c => { const b = document.createElement('button'); b.type = 'button'; b.textContent = c; b.onclick = () => send(c); w.appendChild(b); });
    log.appendChild(w); log.scrollTop = 1e6;
  };
  async function send(v) {
    log.querySelectorAll('.chips-b').forEach(e => e.remove());
    add(esc(v), true);
    const typing = add('<span class="dots"><i></i><i></i><i></i></span>');
    try {
      const [r] = await Promise.all([
        fetch('/api/chatbot', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message:v})}).then(x => x.json()),
        new Promise(ok => setTimeout(ok, 600))]);
      typing.remove();
      let h = esc(r.text).replace(/\n/g, '<br>');
      (r.links || []).forEach(l => h += `<a class="ba" href="${esc(l.url)}">📄 ${esc(l.label)}</a>`);
      (r.ads || []).forEach(a => h += `<a class="ba" href="/ad/${a.id}"><b>${esc(a.price)}</b> ${esc(a.title)}<br><small>${esc(a.city)}</small></a>`);
      add(h); chips(r.chips);
    } catch { typing.remove(); add('Connection problem. Please try again.'); }
  }
  $('#bot-toggle').onclick = () => {
    box.hidden = !box.hidden;
    if (!box.hidden && !log.children.length) { add('Hi! 👋 I\'m Bazaar Buddy. How can I help you today?'); chips(['Find an item', 'Sell something', 'Safety tips', 'Contact a seller']); }
    if (!box.hidden) $('#bot-input').focus();
  };
  $('#bot-close').onclick = () => box.hidden = true;
  $('#bot-form').onsubmit = e => { e.preventDefault(); const i = $('#bot-input'), v = i.value.trim(); if (v) { i.value = ''; send(v); } };

  // country code -> phone length rules
  const cc = $('#cc'), ph = $('#phone');
  if (cc && ph) {
    const upd = () => { const o = cc.selectedOptions[0], n = o.dataset.len;
      ph.maxLength = n; ph.pattern = cc.value === '+91' ? '[6-9]\\d{9}' : `\\d{${n}}`; ph.placeholder = `${n} digits`;
      ph.title = `Enter exactly ${n} digits for ${o.dataset.name}` + (cc.value === '+91' ? ' (starts with 6-9)' : ''); };
    cc.onchange = upd; upd(); ph.oninput = () => ph.value = ph.value.replace(/\D/g, '');
  }
  document.addEventListener('click', async e => {
    const t = e.target.closest('.pw-t');
    if (t) { const i = t.previousElementSibling, show = i.type === 'password'; i.type = show ? 'text' : 'password'; t.classList.toggle('on', show); t.setAttribute('aria-pressed', show); t.setAttribute('aria-label', (show ? 'Hide' : 'Show') + ' password'); }
    const th = e.target.closest('.thumbs img'), m = $('#main-img'); if (th && m) m.src = th.src;
    const h = e.target.closest('.heart');
    if (h) { const r = await fetch('/fav/' + h.dataset.id, {method:'POST'}); if (r.redirected) { location.href = r.url; return; } if (r.ok) h.classList.toggle('on', (await r.json()).saved); }
    if (e.target.id === 'share') {
      const d = {title: document.title, url: location.href};
      if (navigator.share) navigator.share(d).catch(() => {});
      else navigator.clipboard.writeText(d.url).then(() => e.target.textContent = 'Link copied ✓');
    }
  });
})();
