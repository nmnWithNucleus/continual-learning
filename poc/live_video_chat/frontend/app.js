/* =====================================================================
   Live Video Chat — V0 frontend (WS3)
   Vanilla JS, no build step. Consumes Contract B (UI <-> backend, same origin):
     GET  /api/config        -> { model_id, video_longest_side, max_clip_seconds,
                                   target_fps, video_mime, greeting, max_new_tokens }
     POST /api/transcribe    -> multipart field `audio`     -> { text, asr_ms }
     POST /api/turn          -> multipart fields `video`(opt)+`text` -> streamed text/plain
                                <answer markdown> 0x1E <metrics JSON> EOF
   iOS Safari notes baked in:
     - HTTPS / secure-context required for getUserMedia (graceful message otherwise).
     - MediaRecorder yields MP4/H.264 on iOS; feature-detect video/mp4.
     - start()->stop() single-Blob capture (NOT timeslice dataavailable chunks).
     - <video> is playsinline+muted+autoplay; capture starts from a user tap.
     - Streaming read via response.body.getReader() (NOT EventSource / for-await).
   ===================================================================== */

'use strict';

(function () {
  // RECORD SEPARATOR (U+001E) splits the turn stream: answer  metrics-json.
  const RS = '';

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);
  const els = {
    statusDot:   $('status-dot'),
    settingsBtn: $('settings-btn'),
    cfgChip:     $('cfg-chip'),
    preview:     $('preview'),
    cameraOverlay: $('camera-overlay'),
    startCamera: $('start-camera'),
    cameraMsg:   $('camera-msg'),
    recBadge:    $('rec-badge'),
    recTime:     $('rec-time'),
    micBtn:      $('mic-btn'),
    recordBtn:   $('record-btn'),
    loadClipBtn: $('load-clip-btn'),
    prerecStatus:$('prerec-status'),
    prerecLabel: $('prerec-label'),
    prerecClear: $('prerec-clear'),
    textBox:     $('text-box'),
    clipInfo:    $('clip-info'),
    sendBtn:     $('send-btn'),
    output:      $('output'),
    usageSlot:   $('usage-slot'),
    feedbackSlot:$('feedback-slot'),
    // modal (reusable, #7 + #8)
    modalRoot:   $('modal-root'),
    modalBackdrop: $('modal-backdrop'),
    modalCard:   $('modal-card'),
    modalClose:  $('modal-close'),
    modalTitle:  $('modal-title'),
    modalBody:   $('modal-body'),
  };

  // ---- markdown availability (vendored libs; graceful fallback) ----
  const MD_OK = (typeof window.marked !== 'undefined' &&
                 typeof window.DOMPurify !== 'undefined' &&
                 typeof window.DOMPurify.sanitize === 'function');
  if (MD_OK && typeof window.marked.setOptions === 'function') {
    // GitHub-flavored line breaks feel natural for chat-style answers.
    window.marked.setOptions({ gfm: true, breaks: true });
  }

  // ---- App state ----
  const state = {
    config: {
      max_clip_seconds: 30,
      target_fps: 2.0,
      video_mime: 'video/mp4',
      greeting: '',
      model_id: '',
      video_longest_side: 0,
      max_new_tokens: 0,
      prerecorded_clips: [],  // [{id,label,description,duration_s,fps,longest_side}]
      model_presets: [],      // [{id,label,description,est_video_tokens}]
      default_preset_id: '',
    },
    // Active serving preset + whether a reconfigure (vLLM reload) is in flight.
    activePresetId: null,
    modelLoading: false,
    cfgPollId: null,
    // Pre-recorded clip mode: when set, /api/turn is sent with `clip_id` (the recorded
    // camera clip is ignored) and the clip stays loaded across turns (sticky) so you can
    // ask many questions about the same video — which the vLLM prefix cache rewards.
    preRecordedClipId: null,
    preRecordedLabel: '',
    lastTurn: null,         // {turn_id, clip_id, question, answer} for the feedback control
    cameraStream: null,     // MediaStream for the camera preview (video only)
    videoRecorder: null,    // MediaRecorder for the clip
    videoChunks: [],        // accumulated Blob parts (single chunk expected on iOS)
    clipBlob: null,         // the recorded MP4 clip
    recording: false,       // video recording in progress
    recTimerId: null,       // setInterval id for the countdown
    recAutoStopId: null,    // setTimeout id for the hard auto-stop
    recStartedAt: 0,
    audioStream: null,      // short-lived stream for the mic (ASR)
    audioRecorder: null,
    audioChunks: [],
    micRecording: false,
    sending: false,         // a /api/turn stream is in flight
    lastAsrMs: null,        // asr_ms from the most recent /api/transcribe (null if user typed)
    askedByVoice: false,    // did the pending text come from ASR? (resets when user types)
  };

  // ---- status dot helper ----
  function setStatus(kind) {
    // kind: 'idle' | 'busy' | 'rec' | 'error' | 'ok'
    els.statusDot.className = 'dot dot--' + kind;
  }

  // ---- secure-context / capability check ----
  function mediaSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  /* -------------------------------------------------------------------
     mimeType selection for video.
     On iOS Safari only 'video/mp4' is supported; elsewhere (desktop dev)
     we may need to fall back to webm so local validation works. We ALWAYS
     prefer mp4 (the contract + the real target device).
     ------------------------------------------------------------------- */
  function pickVideoMime() {
    const prefer = (state.config.video_mime || 'video/mp4');
    const canDetect = (typeof MediaRecorder !== 'undefined' &&
                       typeof MediaRecorder.isTypeSupported === 'function');
    const candidates = [
      prefer,
      'video/mp4',
      'video/mp4;codecs=h264',
      'video/mp4;codecs=avc1',
      'video/webm;codecs=h264',
      'video/webm;codecs=vp9',
      'video/webm;codecs=vp8',
      'video/webm',
    ];
    if (!canDetect) {
      // Very old/odd engine — hand back the preferred type and hope.
      return prefer;
    }
    for (const t of candidates) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return ''; // let the browser choose its default
  }

  function pickAudioMime() {
    const canDetect = (typeof MediaRecorder !== 'undefined' &&
                       typeof MediaRecorder.isTypeSupported === 'function');
    // iOS records audio as mp4/aac; desktop Chrome/FF use webm/opus.
    const candidates = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mpeg'];
    if (!canDetect) return 'audio/mp4';
    for (const t of candidates) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
  }

  // =====================================================================
  // Output / answer rendering
  //   - Plain modes (greeting, spinner, error): output.plain, textContent.
  //   - Streaming answer: markdown via marked+DOMPurify, throttled by rAF.
  // =====================================================================
  function resetOutput() {
    els.output.classList.remove('is-error', 'is-streaming', 'plain');
    els.output.innerHTML = '';
    clearUsageChip();
  }
  function clearUsageChip() {
    if (els.usageSlot) els.usageSlot.innerHTML = '';
  }
  function showGreeting() {
    resetOutput();
    els.output.classList.add('plain');
    const span = document.createElement('span');
    span.className = 'greeting';
    span.textContent = state.config.greeting || '👋 Hey — show me something and ask.';
    els.output.appendChild(span);
  }
  function showSpinner(label) {
    resetOutput();
    els.output.classList.add('plain');
    const wrap = document.createElement('span');
    wrap.className = 'spinner';
    const ring = document.createElement('span');
    ring.className = 'ring';
    const txt = document.createElement('span');
    txt.textContent = label || 'thinking…';
    wrap.appendChild(ring);
    wrap.appendChild(txt);
    els.output.appendChild(wrap);
  }
  function showError(msg) {
    resetOutput();
    els.output.classList.add('is-error', 'plain');
    els.output.textContent = msg;
    setStatus('error');
  }

  /* Render `text` as markdown (sanitized). Falls back to plain text if the
     vendored libs are missing or parsing throws — the app never breaks. */
  function renderAnswer(text) {
    if (MD_OK) {
      try {
        const html = window.DOMPurify.sanitize(window.marked.parse(text));
        els.output.classList.remove('plain');
        els.output.innerHTML = html;
        return;
      } catch (err) {
        console.warn('[markdown] parse/sanitize failed, falling back to text:', err);
      }
    }
    els.output.classList.add('plain');
    els.output.textContent = text;
  }

  // ---- markdown render throttle (rAF-coalesced) ----
  const answerRender = {
    pending: false,
    text: '',
    schedule(text) {
      this.text = text;
      if (this.pending) return;
      this.pending = true;
      requestAnimationFrame(() => {
        this.pending = false;
        renderAnswer(this.text);
        maybeAutoScroll();
      });
    },
    flush(text) {
      this.pending = false;
      this.text = text;
      renderAnswer(text);
      maybeAutoScroll();
    },
  };

  // =====================================================================
  // Auto-scroll: stick to the bottom of the PAGE while the user is near it;
  // don't yank the page if they've scrolled up to read.
  // =====================================================================
  const NEAR_BOTTOM_PX = 120;
  function isNearPageBottom() {
    const doc = document.documentElement;
    const dist = doc.scrollHeight - (window.scrollY + window.innerHeight);
    return dist <= NEAR_BOTTOM_PX;
  }
  let stickToBottom = true;
  function maybeAutoScroll() {
    if (stickToBottom) {
      window.scrollTo(0, document.documentElement.scrollHeight);
    }
  }
  // Track the user's intent: if they scroll away from the bottom, stop sticking.
  window.addEventListener('scroll', () => {
    stickToBottom = isNearPageBottom();
  }, { passive: true });

  // =====================================================================
  // 1) LOAD: GET /api/config + greeting
  // =====================================================================
  async function loadConfig() {
    try {
      const res = await fetch('/api/config', { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const cfg = await res.json();
      // Merge over defaults; read shared constants from config (never hardcoded).
      if (typeof cfg.max_clip_seconds === 'number' && cfg.max_clip_seconds > 0) {
        state.config.max_clip_seconds = cfg.max_clip_seconds;
      }
      if (typeof cfg.target_fps === 'number' && cfg.target_fps > 0) {
        state.config.target_fps = cfg.target_fps;
      }
      if (typeof cfg.video_mime === 'string' && cfg.video_mime) {
        state.config.video_mime = cfg.video_mime;
      }
      if (typeof cfg.greeting === 'string') {
        state.config.greeting = cfg.greeting;
      }
      if (typeof cfg.model_id === 'string') {
        state.config.model_id = cfg.model_id;
      }
      if (typeof cfg.video_longest_side === 'number' && cfg.video_longest_side > 0) {
        state.config.video_longest_side = cfg.video_longest_side;
      }
      if (typeof cfg.max_new_tokens === 'number' && cfg.max_new_tokens > 0) {
        state.config.max_new_tokens = cfg.max_new_tokens;
      }
      if (Array.isArray(cfg.prerecorded_clips)) {
        state.config.prerecorded_clips = cfg.prerecorded_clips;
      }
      if (Array.isArray(cfg.model_presets)) {
        state.config.model_presets = cfg.model_presets;
      }
      if (typeof cfg.default_preset_id === 'string') {
        state.config.default_preset_id = cfg.default_preset_id;
      }
    } catch (err) {
      // Non-fatal: keep sane defaults so the UI still works for local dev.
      console.warn('[config] failed, using defaults:', err);
    }
    // Enable the pre-recorded loader only when the server offers at least one clip.
    if (els.loadClipBtn) {
      els.loadClipBtn.disabled = !(state.config.prerecorded_clips || []).length;
    }
    // Initialize the model-config chip from the current serving state.
    refreshModelStatus();
    showGreeting();
    setStatus('idle');
  }

  // =====================================================================
  // Pre-recorded clip mode (sanity test): load a long server-side clip,
  // then ask questions about it. The clip is "sticky" across turns.
  // =====================================================================
  function loadPreRecordedClip() {
    const clips = state.config.prerecorded_clips || [];
    if (!clips.length) return;
    // One clip today -> load it directly. (Multiple -> load the first; a picker is a
    // trivial future add.)
    const clip = clips[0];
    state.preRecordedClipId = clip.id;
    state.preRecordedLabel = clip.label || clip.id;
    // A loaded pre-recorded clip supersedes any recorded camera clip.
    state.clipBlob = null;
    state.videoChunks = [];
    renderPrerecStatus();
    refreshSendEnabled();
    setStatus('ok');
  }

  function clearPreRecordedClip() {
    state.preRecordedClipId = null;
    state.preRecordedLabel = '';
    renderPrerecStatus();
    refreshSendEnabled();
  }

  function renderPrerecStatus() {
    if (!els.prerecStatus) return;
    if (state.preRecordedClipId) {
      els.prerecLabel.textContent = state.preRecordedLabel;
      els.prerecStatus.classList.remove('hidden');
    } else {
      els.prerecStatus.classList.add('hidden');
      els.prerecLabel.textContent = '';
    }
  }

  // =====================================================================
  // 2) CAMERA preview (tap-to-start; video only — audio captured separately)
  // =====================================================================
  async function startCamera() {
    if (!mediaSupported()) {
      els.cameraMsg.textContent = (location.protocol !== 'https:' && !isLocalhost())
        ? 'Camera needs HTTPS. Open this page over the secure (https://) URL.'
        : 'Camera API unavailable in this browser.';
      return;
    }
    els.startCamera.disabled = true;
    els.cameraMsg.textContent = 'Starting camera…';
    try {
      // Prefer the rear camera on phones; audio:false so we don't fight the mic.
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      });
      state.cameraStream = stream;
      els.preview.srcObject = stream;
      // iOS sometimes needs an explicit play() even with autoplay.
      try { await els.preview.play(); } catch (_) { /* autoplay attr covers it */ }
      els.cameraOverlay.classList.add('hidden');
      els.recordBtn.disabled = false;
      els.micBtn.disabled = false;
      setStatus('ok');
    } catch (err) {
      els.startCamera.disabled = false;
      els.cameraMsg.textContent = cameraErrorMessage(err);
      setStatus('error');
      console.warn('[camera] getUserMedia failed:', err);
    }
  }

  function isLocalhost() {
    const h = location.hostname;
    return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1';
  }

  function cameraErrorMessage(err) {
    const name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return 'Camera permission denied. Allow camera access and tap to start again.';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      return 'No camera found on this device.';
    }
    if (name === 'NotReadableError') {
      return 'Camera is busy (used by another app). Close it and retry.';
    }
    return 'Could not start the camera. Tap to try again.';
  }

  // =====================================================================
  // 3) VIDEO recording: start()->stop() single Blob, auto-stop at max.
  // =====================================================================
  function startVideoRecording() {
    if (!state.cameraStream || state.recording) return;
    if (typeof MediaRecorder === 'undefined') {
      showError('MediaRecorder not supported in this browser.');
      return;
    }
    const mime = pickVideoMime();
    let recorder;
    try {
      recorder = mime ? new MediaRecorder(state.cameraStream, { mimeType: mime })
                      : new MediaRecorder(state.cameraStream);
    } catch (err) {
      // Some engines reject the options object — retry bare.
      try { recorder = new MediaRecorder(state.cameraStream); }
      catch (e2) { showError('Cannot record video: ' + (e2.message || e2)); return; }
    }

    state.videoRecorder = recorder;
    state.videoChunks = [];
    state.clipBlob = null;

    recorder.ondataavailable = (ev) => {
      // On iOS this typically fires exactly once (at stop). We still
      // collect any parts and assemble a single Blob in onstop.
      if (ev.data && ev.data.size > 0) state.videoChunks.push(ev.data);
    };
    recorder.onstop = () => {
      const blobType = (recorder.mimeType || mime || 'video/mp4');
      state.clipBlob = new Blob(state.videoChunks, { type: blobType });
      onClipReady(state.clipBlob);
    };
    recorder.onerror = (ev) => {
      stopVideoRecording();
      showError('Recording error: ' + ((ev.error && ev.error.message) || 'unknown'));
    };

    // IMPORTANT: no timeslice argument -> single dataavailable on iOS.
    recorder.start();
    state.recording = true;
    state.recStartedAt = Date.now();

    els.recordBtn.textContent = '■ Stop';
    els.recordBtn.classList.add('is-recording');
    els.micBtn.disabled = true;       // one capture at a time
    els.recBadge.classList.remove('hidden');
    setStatus('rec');
    refreshSendEnabled();

    // Visible elapsed / countdown.
    updateRecTime();
    state.recTimerId = setInterval(updateRecTime, 100);

    // Hard auto-stop at max_clip_seconds (read from config).
    const maxMs = state.config.max_clip_seconds * 1000;
    state.recAutoStopId = setTimeout(() => stopVideoRecording(), maxMs);
  }

  function updateRecTime() {
    const elapsed = (Date.now() - state.recStartedAt) / 1000;
    const max = state.config.max_clip_seconds;
    const remaining = Math.max(0, max - elapsed);
    els.recTime.textContent = remaining.toFixed(1) + 's left';
  }

  function stopVideoRecording() {
    if (!state.recording) return;
    state.recording = false;
    if (state.recTimerId)   { clearInterval(state.recTimerId); state.recTimerId = null; }
    if (state.recAutoStopId){ clearTimeout(state.recAutoStopId); state.recAutoStopId = null; }
    try {
      if (state.videoRecorder && state.videoRecorder.state !== 'inactive') {
        state.videoRecorder.stop(); // triggers onstop -> assembles Blob
      }
    } catch (err) {
      console.warn('[record] stop failed:', err);
    }
    els.recordBtn.textContent = '● Record';
    els.recordBtn.classList.remove('is-recording');
    els.micBtn.disabled = false;
    els.recBadge.classList.add('hidden');
    setStatus('ok');
    refreshSendEnabled();
  }

  function onClipReady(blob) {
    const kb = blob.size / 1024;
    const sizeStr = kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : Math.round(kb) + ' KB';
    els.clipInfo.textContent = 'Clip ready · ' + sizeStr;
    els.clipInfo.classList.add('has-clip');
    refreshSendEnabled();
  }

  // =====================================================================
  // 4) MIC / ASR: short audio-only capture -> POST /api/transcribe
  // =====================================================================
  async function startMicRecording() {
    if (state.micRecording || state.recording || state.sending) return;
    if (!mediaSupported()) { showError('Microphone API unavailable (needs HTTPS).'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      state.audioStream = stream;
      const mime = pickAudioMime();
      let rec;
      try {
        rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      } catch (_) { rec = new MediaRecorder(stream); }

      state.audioRecorder = rec;
      state.audioChunks = [];
      rec.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) state.audioChunks.push(ev.data); };
      rec.onstop = () => {
        const type = rec.mimeType || mime || 'audio/mp4';
        const blob = new Blob(state.audioChunks, { type });
        releaseAudioStream();
        if (blob.size > 0) transcribe(blob);
        else setStatus('ok');
      };
      rec.start(); // single Blob pattern (no timeslice)
      state.micRecording = true;
      els.micBtn.classList.add('is-recording');
      els.micBtn.textContent = '🔴 Listening… release';
      els.recordBtn.disabled = true;
      setStatus('rec');
    } catch (err) {
      releaseAudioStream();
      showError('Microphone error: ' + (err.name || err.message || 'unknown'));
    }
  }

  function stopMicRecording() {
    if (!state.micRecording) return;
    state.micRecording = false;
    els.micBtn.classList.remove('is-recording');
    els.micBtn.textContent = '🎤 Hold to ask';
    els.recordBtn.disabled = false;
    try {
      if (state.audioRecorder && state.audioRecorder.state !== 'inactive') {
        state.audioRecorder.stop(); // triggers onstop -> transcribe
      }
    } catch (err) {
      console.warn('[mic] stop failed:', err);
      releaseAudioStream();
    }
  }

  function releaseAudioStream() {
    if (state.audioStream) {
      state.audioStream.getTracks().forEach((t) => t.stop());
      state.audioStream = null;
    }
    state.audioRecorder = null;
  }

  async function transcribe(audioBlob) {
    setStatus('busy');
    els.micBtn.disabled = true;
    const prevPlaceholder = els.textBox.placeholder;
    els.textBox.placeholder = 'Transcribing…';
    try {
      const fd = new FormData();
      // Field name `audio` per Contract B. Provide a filename + type hint.
      const ext = (audioBlob.type.indexOf('mp4') !== -1) ? 'mp4' : 'webm';
      fd.append('audio', audioBlob, 'ask.' + ext);
      const res = await fetch('/api/transcribe', { method: 'POST', body: fd });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      const text = (data && typeof data.text === 'string') ? data.text.trim() : '';
      // Track asr_ms for the usage modal (this turn was asked by voice).
      state.lastAsrMs = (data && typeof data.asr_ms === 'number') ? data.asr_ms : null;
      if (text) {
        // Put returned text in the editable box (append if user already typed).
        els.textBox.value = els.textBox.value
          ? (els.textBox.value.trim() + ' ' + text)
          : text;
        state.askedByVoice = true;
      }
      setStatus('ok');
    } catch (err) {
      console.warn('[transcribe] failed:', err);
      showError('Transcription failed: ' + (err.message || err));
    } finally {
      els.textBox.placeholder = prevPlaceholder;
      els.micBtn.disabled = false;
      refreshSendEnabled();
    }
  }

  // =====================================================================
  // 5) SEND: POST /api/turn (multipart video?+text) -> stream tokens back
  //    Uses fetch() + response.body.getReader() (NOT EventSource/for-await).
  //    Stream framing: <answer markdown>  <metrics JSON>  EOF.
  //    (1) Text-only send: enabled with EITHER a clip OR non-empty text.
  // =====================================================================
  function hasText() {
    return !!(els.textBox.value && els.textBox.value.trim());
  }
  function refreshSendEnabled() {
    const hasClip = !!(state.clipBlob || state.preRecordedClipId);
    const ready = (hasClip || hasText());
    els.sendBtn.disabled = !(ready && !state.sending && !state.recording && !state.micRecording) || state.modelLoading;
    // Helper text reflects the active video source (pre-recorded > recorded > text-only).
    if (state.preRecordedClipId) {
      els.clipInfo.classList.add('has-clip');
      els.clipInfo.textContent = 'Pre-recorded clip loaded';
    } else if (!state.clipBlob) {
      els.clipInfo.classList.remove('has-clip');
      els.clipInfo.textContent = hasText() ? 'No clip — sending text only' : 'No clip yet';
    }
  }

  // Once the model answers, clear the question + recorded clip. A LOADED pre-recorded
  // clip stays sticky (so you can keep asking about the same video); only the recorded
  // camera clip + typed/voiced question reset.
  function resetTurnInputs() {
    els.textBox.value = '';
    state.clipBlob = null;
    state.videoChunks = [];
    state.askedByVoice = false;
    state.lastAsrMs = null;
    if (state.preRecordedClipId) {
      renderPrerecStatus();
      els.clipInfo.classList.add('has-clip');
      els.clipInfo.textContent = 'Pre-recorded clip loaded';
    } else {
      els.clipInfo.classList.remove('has-clip');
      els.clipInfo.textContent = 'No clip yet';
    }
  }

  async function sendTurn() {
    if (state.sending) return;
    if (!state.clipBlob && !state.preRecordedClipId && !hasText()) return;   // clip OR text
    state.sending = true;
    setControlsBusy(true);
    setStatus('busy');
    showSpinner('thinking…');
    clearUsageChip();
    clearFeedback();
    stickToBottom = true;

    // Capture the question NOW — resetTurnInputs() clears the box after the answer, but
    // the feedback record needs the exact text that was asked.
    const sentText = els.textBox.value || '';
    const fd = new FormData();
    if (state.preRecordedClipId) {
      // Pre-recorded clip supersedes any recorded camera clip (matches the backend).
      fd.append('clip_id', state.preRecordedClipId);
    } else if (state.clipBlob) {
      const ext = (state.clipBlob.type.indexOf('mp4') !== -1) ? 'mp4' : 'webm';
      fd.append('video', state.clipBlob, 'turn.' + ext);
    }
    fd.append('text', sentText);

    // asr_ms applies only if the text for THIS turn came from voice.
    const turnAsrMs = state.askedByVoice ? state.lastAsrMs : null;
    const sendStartedAt = performance.now();
    let firstTokenMs = null;

    let firstToken = true;
    let answer = '';        // accumulated answer text (before the RS)
    let metricsBuf = '';    // accumulated bytes after the RS (metrics JSON)
    let sawRS = false;

    function onFirstToken() {
      if (!firstToken) return;
      firstToken = false;
      firstTokenMs = Math.round(performance.now() - sendStartedAt);
      resetOutput();
      els.output.classList.add('is-streaming');
    }

    try {
      const res = await fetch('/api/turn', { method: 'POST', body: fd });
      if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + res.statusText);

      if (!res.body || typeof res.body.getReader !== 'function') {
        // Fallback for engines without streaming body support.
        const txt = await res.text();
        const i = txt.indexOf(RS);
        if (i === -1) { answer = txt; }
        else { answer = txt.slice(0, i); metricsBuf = txt.slice(i + 1); }
        onFirstToken();
        answerRender.flush(answer);
      } else {
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        // Stream loop: read chunks manually, decode incrementally.
        // (No `for await...of` — not available before Safari 26.4.)
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!value) continue;
          const chunk = decoder.decode(value, { stream: true });
          if (!chunk) continue;
          consumeChunk(chunk);
        }
        // Flush any buffered multibyte remainder.
        const tail = decoder.decode();
        if (tail) consumeChunk(tail);

        if (firstToken) {
          // Stream closed with no content at all.
          onFirstToken();
          answerRender.flush('(no response)');
        } else {
          answerRender.flush(answer);   // final, complete render
        }
      }

      // Done streaming.
      els.output.classList.remove('is-streaming');
      const metrics = parseMetrics(metricsBuf);
      // Only show the usage chip + feedback when metrics arrived (and not an [error] turn).
      if (metrics && !isErrorAnswer(answer)) {
        mountUsageChip(metrics, { asrMs: turnAsrMs, sendToFirstTokenMs: firstTokenMs });
        // Per-turn thumbs up/down, joined to this exact turn for later fine-tuning data.
        state.lastTurn = {
          turn_id: metrics.turn_id || '',
          clip_id: metrics.clip_id || '',
          question: sentText,
          answer: answer,
        };
        mountFeedback(state.lastTurn);
      }
      maybeAutoScroll();
      setStatus('ok');
      resetTurnInputs();   // clear question + recorded clip (pre-recorded stays loaded)
    } catch (err) {
      console.warn('[turn] failed:', err);
      showError('Request failed: ' + (err.message || err));
    } finally {
      state.sending = false;
      setControlsBusy(false);
      refreshSendEnabled();
    }

    // Helper: route a decoded chunk into answer vs metrics, splitting on RS.
    // The RS may arrive mid-chunk; once seen, everything after is metrics.
    function consumeChunk(chunk) {
      if (sawRS) { metricsBuf += chunk; return; }
      const i = chunk.indexOf(RS);
      if (i === -1) {
        if (chunk) onFirstToken();
        answer += chunk;
        answerRender.schedule(answer);
      } else {
        const head = chunk.slice(0, i);
        if (answer || head) onFirstToken();
        answer += head;
        sawRS = true;
        metricsBuf += chunk.slice(i + 1);
        answerRender.schedule(answer);   // render the now-final answer text
      }
    }
  }

  function isErrorAnswer(answer) {
    return /^\s*\[error\]/i.test(answer || '');
  }

  function parseMetrics(buf) {
    const s = (buf || '').trim();
    if (!s) return null;
    try {
      const m = JSON.parse(s);
      return (m && typeof m === 'object') ? m : null;
    } catch (err) {
      console.warn('[metrics] parse failed:', err, s);
      return null;
    }
  }

  function setControlsBusy(busy) {
    const ready = (state.clipBlob || state.preRecordedClipId || hasText());
    els.sendBtn.disabled = busy || !ready;
    els.recordBtn.disabled = busy || !state.cameraStream;
    els.micBtn.disabled = busy || !state.cameraStream;
    els.textBox.disabled = busy;
    if (els.loadClipBtn) {
      els.loadClipBtn.disabled = busy || !((state.config.prerecorded_clips || []).length);
    }
    if (els.prerecClear) els.prerecClear.disabled = busy;
    els.settingsBtn.disabled = false; // settings always reachable
  }

  // =====================================================================
  // 7) Settings modal — config snapshot
  // =====================================================================
  function openSettingsModal() {
    const c = state.config;
    const body = document.createElement('div');

    const rows = [
      ['Model', codeEl(c.model_id || '—')],
      ['Video FPS', textNode(fmtNum(c.target_fps))],
      ['Max video length',
        textNode((c.max_clip_seconds ? c.max_clip_seconds + ' s' : '—') +
                 (c.target_fps ? '  @ ' + fmtNum(c.target_fps) + ' fps' : ''))],
      ['Resolution fed to model',
        textNode(c.video_longest_side ? (c.video_longest_side + ' px (longest side)') : '—')],
    ];
    for (const [k, vNode] of rows) body.appendChild(kvRow(k, vNode));

    openModal('Settings', body);
  }

  // =====================================================================
  // Model config picker: switch the vLLM video-processing preset. Switching
  // relaunches vLLM (~3-4 min); poll /api/model/status, show spinner -> green
  // check. Send is disabled while a reconfigure is in flight.
  // =====================================================================
  function shortPreset(id) {
    const p = (state.config.model_presets || []).find((x) => x.id === id);
    return p ? p.label : (id || '—');
  }

  async function refreshModelStatus() {
    try {
      const res = await fetch('/api/model/status', { headers: { Accept: 'application/json' } });
      if (!res.ok) return null;
      const st = await res.json();
      state.activePresetId = st.preset_id || null;
      state.modelLoading = (st.state === 'loading');
      updateCfgChip(st);
      return st;
    } catch (_) { return null; }
  }

  function updateCfgChip(st) {
    if (!els.cfgChip) return;
    if (st && st.state === 'loading') {
      els.cfgChip.textContent = '⟳ loading…';
      els.cfgChip.classList.add('is-loading');
      return;
    }
    els.cfgChip.classList.remove('is-loading');
    if (st && (st.state === 'down' || st.vllm_up === false)) {
      els.cfgChip.textContent = 'model down';
    } else {
      els.cfgChip.textContent = 'cfg: ' + ((st && st.label) || shortPreset(state.activePresetId) || '—');
    }
  }

  function openConfigModal() {
    const presets = state.config.model_presets || [];
    const body = document.createElement('div');

    const note = document.createElement('p');
    note.className = 'modal-note';
    note.style.fontStyle = 'normal';
    note.textContent = 'Switching a preset relaunches the model (~3–4 min). The app is unavailable until the green check appears.';
    body.appendChild(note);

    const statusEl = document.createElement('div');
    body.appendChild(statusEl);

    const list = document.createElement('div');
    list.className = 'preset-list';
    body.appendChild(list);

    function renderCfgStatus() {
      statusEl.className = 'cfg-status ' + (state.modelLoading ? 'is-loading' : 'is-ready');
      statusEl.innerHTML = '';
      if (state.modelLoading) {
        const ring = document.createElement('span'); ring.className = 'ring';
        statusEl.appendChild(ring);
        statusEl.appendChild(document.createTextNode(' Loading ' + shortPreset(state.activePresetId) + '… (model reload ~3–4 min)'));
      } else {
        statusEl.textContent = '✓ Ready: ' + shortPreset(state.activePresetId);
      }
    }

    function render() {
      list.innerHTML = '';
      for (const p of presets) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'preset-btn';
        const active = (p.id === state.activePresetId);
        if (active) btn.classList.add('is-active');
        const top = document.createElement('div');
        top.className = 'preset-top';
        top.textContent = (active ? '✓ ' : '') + p.label;
        const desc = document.createElement('div');
        desc.className = 'preset-desc';
        desc.textContent = p.description || '';
        btn.appendChild(top); btn.appendChild(desc);
        btn.disabled = state.modelLoading || active;
        btn.addEventListener('click', () => selectPreset(p));
        list.appendChild(btn);
      }
      renderCfgStatus();
    }

    render();
    state._cfgRender = render;   // let the poller refresh this modal while open
    openModal('Model config', body);
  }

  async function selectPreset(preset) {
    if (state.modelLoading) return;
    state.modelLoading = true;
    state.activePresetId = preset.id;      // optimistic: show which one is loading
    if (state._cfgRender) state._cfgRender();
    setControlsBusy(true);
    updateCfgChip({ state: 'loading' });
    try {
      const res = await fetch('/api/model/reconfigure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset_id: preset.id }),
      });
      if (!res.ok && res.status !== 409) throw new Error('HTTP ' + res.status);
    } catch (err) {
      console.warn('[reconfigure] failed:', err);
    }
    if (state.cfgPollId) clearInterval(state.cfgPollId);
    state.cfgPollId = setInterval(async () => {
      const st = await refreshModelStatus();
      if (!st || st.state === 'loading') return;
      clearInterval(state.cfgPollId); state.cfgPollId = null;
      state.modelLoading = false;
      setControlsBusy(false);
      refreshSendEnabled();
      if (state._cfgRender) state._cfgRender();
    }, 5000);
  }

  // =====================================================================
  // 8) Per-turn usage modal (mounted via the chip under an answer)
  // =====================================================================
  function mountUsageChip(metrics, client) {
    clearUsageChip();
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'usage-chip';
    const i = document.createElement('span');
    i.className = 'chip-i';
    i.textContent = 'i';
    chip.appendChild(i);
    chip.appendChild(document.createTextNode('usage'));
    chip.addEventListener('click', () => openUsageModal(metrics, client));
    els.usageSlot.appendChild(chip);
  }

  function openUsageModal(metrics, client) {
    const tok = (metrics && metrics.tokens) || {};
    const tim = (metrics && metrics.timing_ms) || {};
    const body = document.createElement('div');

    // ----- Input tokens -----
    body.appendChild(sectionTitle('Input tokens'));
    body.appendChild(kvRow('System', textNode(fmtInt(tok.system))));
    body.appendChild(kvRow('Video', textNode(fmtInt(tok.video))));
    body.appendChild(kvRow('Text', textNode(fmtInt(tok.text))));
    body.appendChild(kvRow('Total prompt', textNode(fmtInt(tok.prompt_total))));
    body.appendChild(note(
      'Audio is transcribed to text by Whisper (ASR) and is NOT sent to the model — ' +
      'no separate audio tokens.'));

    // ----- Output tokens -----
    body.appendChild(sectionTitle('Output tokens'));
    body.appendChild(kvRow('Output', textNode(fmtInt(tok.output))));

    // ----- Timings -----
    body.appendChild(sectionTitle('Timings'));
    body.appendChild(kvRow('ASR', textNode(client && client.asrMs != null ? fmtMs(client.asrMs) : '—')));
    body.appendChild(kvRow('Send→first token',
      textNode(client && client.sendToFirstTokenMs != null ? fmtMs(client.sendToFirstTokenMs) : '—')));
    body.appendChild(kvRow('Model TTFT', textNode(fmtMs(tim.ttft))));
    body.appendChild(kvRow('Inference', textNode(fmtMs(tim.inference_total))));
    body.appendChild(kvRow('Normalize', textNode(fmtMs(tim.normalize))));

    openModal('Turn usage', body);
  }

  // =====================================================================
  // Per-turn feedback (thumbs up/down) — POSTed to /api/feedback and saved
  // server-side for later fine-tuning/eval. One rating per turn; the buttons
  // lock after a choice (retry allowed only if the save fails).
  // =====================================================================
  function clearFeedback() {
    if (els.feedbackSlot) els.feedbackSlot.innerHTML = '';
  }

  function mountFeedback(turn) {
    if (!els.feedbackSlot) return;
    els.feedbackSlot.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'feedback';

    const q = document.createElement('span');
    q.className = 'feedback-q';
    q.textContent = 'Was this right?';
    wrap.appendChild(q);

    const mkBtn = (glyph, rating, aria) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'feedback-btn';
      b.textContent = glyph;
      b.setAttribute('aria-label', aria);
      b.addEventListener('click', () => submitFeedback(turn, rating, wrap, b));
      return b;
    };
    wrap.appendChild(mkBtn('👍', 'up', 'Thumbs up'));
    wrap.appendChild(mkBtn('👎', 'down', 'Thumbs down'));

    const status = document.createElement('span');
    status.className = 'feedback-status';
    wrap.appendChild(status);

    els.feedbackSlot.appendChild(wrap);
  }

  async function submitFeedback(turn, rating, wrap, btn) {
    const status = wrap.querySelector('.feedback-status');
    // Lock both buttons immediately; highlight the chosen one.
    wrap.querySelectorAll('.feedback-btn').forEach((b) => {
      b.disabled = true;
      b.classList.toggle('is-chosen', b === btn);
    });
    if (status) status.textContent = 'saving…';
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          turn_id: (turn && turn.turn_id) || '',
          rating: rating,
          clip_id: (turn && turn.clip_id) || '',
          question: (turn && turn.question) || '',
          answer: (turn && turn.answer) || '',
        }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      if (status) status.textContent = 'thanks — saved';
    } catch (err) {
      console.warn('[feedback] failed:', err);
      if (status) status.textContent = 'save failed — tap to retry';
      wrap.querySelectorAll('.feedback-btn').forEach((b) => { b.disabled = false; });
    }
  }

  // ---- small formatting helpers ----
  function fmtInt(v)  { return (typeof v === 'number' && isFinite(v)) ? String(Math.round(v)) : '—'; }
  function fmtNum(v)  { return (typeof v === 'number' && isFinite(v)) ? String(v) : '—'; }
  function fmtMs(v)   {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    if (v >= 1000) { const s = (v / 1000).toFixed(1); return (s.endsWith('.0') ? s.slice(0, -2) : s) + ' s'; }
    return Math.round(v) + ' ms';
  }
  function textNode(s){ return document.createTextNode(s); }
  function codeEl(s)  { const c = document.createElement('code'); c.textContent = s; return c; }

  function kvRow(key, valueNode) {
    const row = document.createElement('div');
    row.className = 'kv';
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = key;
    const v = document.createElement('span');
    v.className = 'v';
    v.appendChild(valueNode);
    row.appendChild(k);
    row.appendChild(v);
    return row;
  }
  function sectionTitle(s) {
    const el = document.createElement('div');
    el.className = 'modal-section-title';
    el.textContent = s;
    return el;
  }
  function note(s) {
    const el = document.createElement('p');
    el.className = 'modal-note';
    el.textContent = s;
    return el;
  }

  // =====================================================================
  // Reusable modal component (#7 + #8): centered card, backdrop dismiss,
  // ✕ at top-left. ONE implementation shared by both modals.
  // =====================================================================
  function openModal(title, bodyNode) {
    els.modalTitle.textContent = title;
    els.modalBody.innerHTML = '';
    els.modalBody.appendChild(bodyNode);
    els.modalRoot.classList.remove('hidden');
    els.modalRoot.setAttribute('aria-hidden', 'false');
  }
  function closeModal() {
    els.modalRoot.classList.add('hidden');
    els.modalRoot.setAttribute('aria-hidden', 'true');
    els.modalBody.innerHTML = '';
  }

  // =====================================================================
  // Wiring / event listeners
  // =====================================================================
  function wire() {
    els.startCamera.addEventListener('click', startCamera);

    // Record toggles start/stop.
    els.recordBtn.addEventListener('click', () => {
      if (state.recording) stopVideoRecording();
      else startVideoRecording();
    });

    // Push-to-talk: pointer events cover mouse + touch on modern Safari.
    const micDown = (e) => { e.preventDefault(); startMicRecording(); };
    const micUp   = (e) => { e.preventDefault(); stopMicRecording(); };
    if (window.PointerEvent) {
      els.micBtn.addEventListener('pointerdown', micDown);
      els.micBtn.addEventListener('pointerup', micUp);
      els.micBtn.addEventListener('pointercancel', micUp);
      els.micBtn.addEventListener('pointerleave', () => { if (state.micRecording) stopMicRecording(); });
    } else {
      els.micBtn.addEventListener('touchstart', micDown, { passive: false });
      els.micBtn.addEventListener('touchend', micUp);
      els.micBtn.addEventListener('mousedown', micDown);
      els.micBtn.addEventListener('mouseup', micUp);
    }
    // Safety: if focus/window is lost mid-hold, stop the mic.
    window.addEventListener('blur', () => { if (state.micRecording) stopMicRecording(); });

    els.sendBtn.addEventListener('click', sendTurn);

    // Pre-recorded clip loader (sanity-test mode).
    if (els.loadClipBtn) els.loadClipBtn.addEventListener('click', loadPreRecordedClip);
    if (els.prerecClear) els.prerecClear.addEventListener('click', clearPreRecordedClip);

    els.textBox.addEventListener('input', () => {
      // A manual edit means the pending question is no longer voice-only.
      state.askedByVoice = false;
      refreshSendEnabled();
    });

    // Settings modal (#7).
    els.settingsBtn.addEventListener('click', openSettingsModal);

    // Model config picker (topbar chip).
    if (els.cfgChip) els.cfgChip.addEventListener('click', openConfigModal);

    // Modal dismiss: backdrop tap (outside) or the top-left ✕.
    els.modalBackdrop.addEventListener('click', closeModal);
    els.modalClose.addEventListener('click', closeModal);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !els.modalRoot.classList.contains('hidden')) closeModal();
    });
  }

  // ---- boot ----
  function init() {
    if (!mediaSupported()) {
      els.cameraMsg.textContent = (location.protocol !== 'https:' && !isLocalhost())
        ? '⚠️ Camera/mic need HTTPS. Open this page via the secure (https://) URL.'
        : '⚠️ This browser does not support camera/microphone capture.';
      els.startCamera.disabled = true;
    }
    wire();
    loadConfig();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
