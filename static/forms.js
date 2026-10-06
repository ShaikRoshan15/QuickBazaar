/* Shared form helpers: inline hints, password strength meter. Loaded on every page. */
window.QBForm = (() => {
  const GMAIL = /^[A-Za-z0-9._%+-]+@gmail\.com$/;
  const NAME = /^[A-Za-z][A-Za-z .'-]{1,49}$/;
  const hintEl = input => { const l = input.closest('label'); return l ? l.querySelector('[data-hint]') : null; };
  // state: 'ok' | 'bad' | 'busy' | '' (neutral)
  function hint(input, msg, state) {
    const h = hintEl(input);
    if (h) { h.textContent = msg || ''; h.className = 'hint' + (state ? ' ' + state : ''); }
    input.classList.toggle('bad', state === 'bad'); input.classList.toggle('good', state === 'ok');
    input.setAttribute('aria-invalid', state === 'bad' ? 'true' : 'false');
  }
  const pwIssue = pw => pw.length < 8 ? 'Use at least 8 characters.' : !/[A-Za-z]/.test(pw) ? 'Add at least one letter.' : !/\d/.test(pw) ? 'Add at least one number.' : '';
  function strength(pw) {
    let n = 0; if (pw.length >= 8) n++; if (pw.length >= 12) n++; if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) n++; if (/\d/.test(pw)) n++; if (/[^A-Za-z0-9]/.test(pw)) n++;
    return pw ? Math.max(1, Math.min(4, n)) : 0;
  }
  function meter(input) {
    const l = input.closest('label'), m = l && l.querySelector('.meter'); if (!m) return;
    m.dataset.s = strength(input.value);
  }
  return { GMAIL, NAME, hint, pwIssue, strength, meter };
})();

/* Photo shrinker: phone photos are often 3-8 MB, but QuickBazaar stores images in Cloudflare D1 (1.35 MB max each).
   Before a form with photos is submitted, large images are resized (max 1600 px) and re-encoded as JPEG in the browser.
   Progressive enhancement: if anything fails the original file is sent and the server still validates the size. */
(() => {
  const MAX_DIM = 1600, QUALITY = 0.82, SKIP_BELOW = 350 * 1024;
  const shrinkable = f => /^image\/(jpeg|png|webp)$/.test(f.type) && f.size >= SKIP_BELOW;
  async function shrink(file) {
    if (!shrinkable(file)) return file;
    try {
      const bmp = await createImageBitmap(file);
      const k = Math.min(1, MAX_DIM / Math.max(bmp.width, bmp.height));
      const w = Math.max(1, Math.round(bmp.width * k)), h = Math.max(1, Math.round(bmp.height * k));
      const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
      const ctx = cv.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.drawImage(bmp, 0, 0, w, h);
      const blob = await new Promise(ok => cv.toBlob(ok, 'image/jpeg', QUALITY));
      if (!blob || blob.size >= file.size) return file;
      return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
    } catch { return file; }
  }
  window.QBForm.shrink = shrink;
  document.addEventListener('submit', async e => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.qbShrunk) return;
    const inputs = [...form.querySelectorAll('input[type=file]')].filter(i => i.files && [...i.files].some(shrinkable));
    if (!inputs.length || !window.DataTransfer || !window.createImageBitmap) return;
    e.preventDefault(); e.stopImmediatePropagation();                 // pause; other handlers run on the re-submit below
    const submitter = e.submitter, label = submitter && submitter.textContent;
    if (submitter) { submitter.disabled = true; submitter.textContent = 'Optimising photos\u2026'; }
    try {
      for (const input of inputs) {
        const dt = new DataTransfer();
        for (const f of input.files) dt.items.add(await shrink(f));
        input.files = dt.files;
      }
    } catch {}
    if (submitter) { submitter.disabled = false; submitter.textContent = label; }
    form.dataset.qbShrunk = '1';
    if (form.requestSubmit) form.requestSubmit(submitter || undefined); else form.submit();
  }, true);
})();
