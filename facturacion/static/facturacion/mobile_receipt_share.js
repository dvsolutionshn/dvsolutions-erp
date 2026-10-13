/* Share only the original protected receipt PDF, after two explicit gestures. */
(() => {
  'use strict';
  const panel = document.getElementById('mobileReceiptShare');
  const prepare = document.getElementById('prepareReceiptShare');
  const share = document.getElementById('shareReceiptFile');
  const status = document.getElementById('receiptShareStatus');
  if (!panel || !prepare || !share || !status) return;

  const MAX_BYTES = 20 * 1024 * 1024;
  const FALLBACK = 'Este navegador no permite compartir este archivo. Utiliza Abrir PDF original.';
  const INITIAL = 'Toca Preparar PDF y, cuando esté listo, Compartir archivo. Tú eliges la app y el destinatario.';
  let preparedFile = null, controller = null, timeout = null, generation = 0, state = 'idle';

  const supported = () => Boolean(window.isSecureContext
    && typeof navigator.share === 'function' && typeof navigator.canShare === 'function'
    && typeof File === 'function' && typeof fetch === 'function'
    && typeof AbortController === 'function');
  const canShare = file => {
    try { return supported() && navigator.canShare({ files: [file] }); }
    catch (_error) { return false; }
  };
  let pdfURL;
  try { pdfURL = new URL(panel.dataset.pdfUrl, window.location.origin); }
  catch (_error) { pdfURL = null; }
  const validURL = () => Boolean(pdfURL
    && ['https:', 'http:'].includes(pdfURL.protocol) && pdfURL.origin === window.location.origin
    && !pdfURL.username && !pdfURL.password && !pdfURL.hash
    && pdfURL.pathname === `${window.location.pathname.replace(/\/$/, '')}/pdf/`
    && pdfURL.search === '?app=1');
  const supportedPDF = () => {
    if (!supported() || !validURL()) return false;
    return canShare(new File(['%PDF-1.4\n%%EOF\n'], 'recibo.pdf', { type: 'application/pdf' }));
  };
  const controls = () => {
    const busy = state === 'preparing' || state === 'sharing';
    panel.setAttribute('aria-busy', String(busy));
    prepare.disabled = busy || !supportedPDF();
    share.disabled = busy || !preparedFile;
  };
  const clearTimeoutHandle = () => {
    if (timeout !== null) window.clearTimeout(timeout);
    timeout = null;
  };
  const discard = message => {
    generation += 1;
    controller?.abort(); controller = null;
    clearTimeoutHandle(); preparedFile = null; state = 'idle';
    controls(); status.textContent = supportedPDF() ? message : FALLBACK;
  };
  const filename = disposition => {
    const match = (disposition || '').match(/(?:^|;)\s*filename\s*=\s*"([^"\r\n]*)"/i);
    let name = match ? match[1].split(/[\\/]/).pop() : 'recibo.pdf';
    name = name.replace(/[\u0000-\u001f\u007f:*?"<>|]/g, '_').replace(/^\.+/, '').trim();
    if (!name || !/\.pdf$/i.test(name)) return 'recibo.pdf';
    return name.length > 180 ? `${name.slice(0, 176)}.pdf` : name;
  };
  const readPDF = async (response, signal) => {
    const length = response.headers.get('Content-Length');
    if (length && /^\d+$/.test(length) && Number(length) > MAX_BYTES) throw new Error('size');
    let blob;
    if (response.body && typeof response.body.getReader === 'function') {
      const reader = response.body.getReader(), chunks = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (signal.aborted) throw new Error('aborted');
          if (done) break;
          size += value.byteLength;
          if (size > MAX_BYTES) { await reader.cancel(); throw new Error('size'); }
          chunks.push(value);
        }
        blob = new Blob(chunks, { type: 'application/pdf' });
      } finally { reader.releaseLock(); }
    } else {
      blob = await response.blob();
    }
    if (signal.aborted || !blob.size || blob.size > MAX_BYTES) throw new Error('invalid');
    const signature = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
    if (signature.length !== 5 || signature.some((value, index) => value !== [37, 80, 68, 70, 45][index])) {
      throw new Error('signature');
    }
    return new File([blob], filename(response.headers.get('Content-Disposition')), { type: 'application/pdf' });
  };

  prepare.addEventListener('click', async event => {
    if (!event.isTrusted || state === 'preparing' || state === 'sharing') return;
    if (!supportedPDF()) { discard(FALLBACK); return; }
    preparedFile = null; state = 'preparing';
    const token = ++generation, requestController = new AbortController();
    controller = requestController; controls(); status.textContent = 'Preparando el PDF original…';
    timeout = window.setTimeout(() => requestController.abort(), 30000);
    try {
      const response = await fetch(pdfURL.href, {
        method: 'GET', credentials: 'same-origin', mode: 'same-origin', redirect: 'error', cache: 'no-store',
        headers: { Accept: 'application/pdf' }, signal: requestController.signal,
      });
      if (response.status !== 200 || !response.ok || response.redirected || response.url !== pdfURL.href
          || (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase() !== 'application/pdf') {
        throw new Error('response');
      }
      const file = await readPDF(response, requestController.signal);
      if (token !== generation) return;
      if (requestController.signal.aborted) throw new Error('aborted');
      if (!canShare(file)) throw new Error('unsupported');
      preparedFile = file; state = 'ready';
      status.textContent = 'PDF listo. Toca Compartir archivo para elegir la app y el destinatario; todavía no se ha compartido.';
    } catch (error) {
      requestController.abort();
      if (token !== generation) return;
      preparedFile = null; state = 'idle';
      status.textContent = error?.message === 'unsupported' ? FALLBACK
        : 'No se pudo preparar el archivo. Vuelve a intentarlo o utiliza Abrir PDF original.';
    } finally {
      if (token === generation) {
        clearTimeoutHandle(); controller = null; controls();
      }
    }
  });
  share.addEventListener('click', async event => {
    if (!event.isTrusted || state !== 'ready' || !preparedFile) return;
    if (!validURL() || !canShare(preparedFile)) { discard(FALLBACK); return; }
    if (navigator.userActivation && !navigator.userActivation.isActive) {
      status.textContent = 'Toca Compartir archivo para abrir el selector del teléfono.'; return;
    }
    const file = preparedFile, token = ++generation;
    preparedFile = null; state = 'sharing'; controls();
    status.textContent = 'Elige la app y el destinatario en el selector del teléfono.';
    try {
      // No await before this call: preserve the fresh click's user activation.
      // No URL, title, text or recipient leaves the app, only this file.
      await navigator.share({ files: [file] });
      if (token === generation) {
        status.textContent = 'Solicitud de compartir terminada. Comprueba el envío en la app elegida; aquí no se confirma la entrega.';
      }
    } catch (error) {
      if (token !== generation) return;
      status.textContent = error?.name === 'AbortError'
        ? 'Compartición cancelada o no disponible. Puedes preparar el PDF nuevamente.'
        : 'No se pudo compartir. Utiliza Abrir PDF original o prepara el archivo nuevamente.';
    } finally {
      if (token === generation) { state = 'idle'; preparedFile = null; controls(); }
    }
  });
  window.addEventListener('pagehide', () => discard(INITIAL));
  window.addEventListener('pageshow', () => discard(INITIAL));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && state !== 'sharing') discard(INITIAL);
  });
  discard(INITIAL);
})();
