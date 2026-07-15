'use strict';

// ── State ──────────────────────────────────────────────────────
const state = {
  sessionId: localStorage.getItem('victoria_session') || null,
  isStreaming: false,
  userId: 'web',
  speakReplies: localStorage.getItem('victoria_speak') === '1',
  speakNext: false,      // set when input came from the mic → speak this reply
  isRecording: false,
  handsFree: false,      // wake-word mode: listen for "Victoria" continuously
  hfPhase: 'idle',       // idle | listening | capturing | processing
  speakPromise: null,    // resolves when the current reply finishes playing
};

// The wake word (lowercase). Anything containing this triggers capture.
const WAKE_WORD = 'victoria';

// ── DOM refs ───────────────────────────────────────────────────
const messagesEl    = document.getElementById('chat-messages');
const welcome       = document.getElementById('welcome');
const inputEl       = document.getElementById('message-input');
const sendBtn       = document.getElementById('send-btn');
const micBtn        = document.getElementById('mic-btn');
const speakBtn      = document.getElementById('speak-btn');
const backendSel    = document.getElementById('backend-select');
const newSessionBtn = document.getElementById('new-session-btn');
const vaultList     = document.getElementById('vault-list');
const vaultName     = document.getElementById('vault-name');
const vaultValue    = document.getElementById('vault-value');
const vaultStoreBtn = document.getElementById('vault-store-btn');
const vaultCount    = document.getElementById('vault-count');
const modelSelect   = document.getElementById('model-select');
const modelHint     = document.getElementById('model-hint');
const statusPill    = document.getElementById('status-pill');
const footerStatus  = document.getElementById('footer-status');
const sessionDisplay= document.getElementById('session-id-display');

// ── Clock ──────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  document.getElementById('clock-h').textContent = pad(now.getHours());
  document.getElementById('clock-m').textContent = pad(now.getMinutes());
  document.getElementById('clock-s').textContent = pad(now.getSeconds());

  const days   = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  document.getElementById('date-display').textContent =
    `${days[now.getDay()]} ${pad(now.getDate())} · ${pad(now.getMonth()+1)} · ${now.getFullYear()}`;
}
updateClock();
setInterval(updateClock, 1000);

// ── Ring tick marks ────────────────────────────────────────────
function buildRingTicks() {
  const g = document.getElementById('ring-ticks');
  if (!g) return;
  for (let i = 0; i < 72; i++) {
    const angle = (i * 5) * Math.PI / 180;
    const isLong = i % 9 === 0;
    const r1 = 192, r2 = isLong ? 183 : 187;
    const x1 = 200 + r1 * Math.cos(angle);
    const y1 = 200 + r1 * Math.sin(angle);
    const x2 = 200 + r2 * Math.cos(angle);
    const y2 = 200 + r2 * Math.sin(angle);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', x1); line.setAttribute('y1', y1);
    line.setAttribute('x2', x2); line.setAttribute('y2', y2);
    line.setAttribute('stroke', isLong ? 'rgba(0,184,212,0.5)' : 'rgba(0,184,212,0.2)');
    line.setAttribute('stroke-width', isLong ? '1' : '0.5');
    g.appendChild(line);
  }
}
buildRingTicks();

// ── Status helpers ─────────────────────────────────────────────
function setStatus(text, color = '') {
  statusPill.textContent = `● ${text}`;
  statusPill.style.color = color || 'var(--green)';
  footerStatus.textContent = text;
}

function setStreaming(on) {
  state.isStreaming = on;
  sendBtn.disabled = on;
  inputEl.disabled = on;
  if (micBtn) micBtn.disabled = on;   // don't record while a reply is streaming
  document.querySelector('.hud').classList.toggle('listening', on);
  if (on) {
    setStatus('PROCESSING', 'var(--teal-bright)');
  } else {
    setStatus('SYSTEM NOMINAL', '');
  }
}

// ── Profile + session data ─────────────────────────────────────
async function loadSidebarData() {
  // Profile
  try {
    const resp = await fetch(`/v1/profile/${state.userId}`);
    if (resp.ok) {
      const p = await resp.json();
      if (p.available) {
        document.getElementById('p-name').textContent    = p.name || 'UNIDENTIFIED';
        document.getElementById('p-address').textContent = p.preferred_address || '—';
        document.getElementById('p-style').textContent   = p.communication_style || '—';
        document.getElementById('p-pref-count').textContent = p.preferences?.length || '0';

        const memList = document.getElementById('memories-list');
        document.getElementById('mem-count').textContent = p.explicit_memories?.length || '0';
        if (p.explicit_memories?.length > 0) {
          memList.replaceChildren(...p.explicit_memories.map(m => {
            const item = document.createElement('div');
            item.className = 'memory-item';
            item.textContent = m;
            return item;
          }));
        }

        // First run: ask who she's assisting (name + how to address them).
        if (!p.onboarded) showOnboarding(p.name);
      }
    }
  } catch (_) {}

  // Sessions → session-log counts + the Topics (chat-history) list
  try {
    const resp = await fetch(`/v1/sessions/${state.userId}`);
    if (resp.ok) {
      const sessions = await resp.json();
      const total = sessions.length;
      const today = new Date().toDateString();
      const todayCount = sessions.filter(s => {
        return s.updated_at && new Date(s.updated_at).toDateString() === today;
      }).length;

      document.getElementById('sess-today').textContent = todayCount;
      document.getElementById('sess-total').textContent = total;
      const pct = Math.min(100, (total / 20) * 100);
      document.getElementById('sess-bar').style.width = pct + '%';

      renderTopics(sessions);
    }
  } catch (_) {}

  // Health / backend
  try {
    const resp = await fetch('/health');
    if (resp.ok) {
      const h = await resp.json();
      document.getElementById('sys-tools').textContent  = h.tools ?? '4';
      document.getElementById('sys-memory').textContent = h.semantic_memory ? 'ACTIVE' : 'OFFLINE';
      document.getElementById('sys-api').textContent    = 'ONLINE';
    }
  } catch (_) {
    document.getElementById('sys-api').textContent = 'ERROR';
  }
}
loadSidebarData();

// ── Topics (chat history) ──────────────────────────────────────
function fmtChatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  if (d.toDateString() === new Date().toDateString()) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}

function renderTopics(sessions) {
  const list  = document.getElementById('topics-list');
  const count = document.getElementById('topics-count');
  if (!list) return;
  count.textContent = sessions.length;
  if (!sessions.length) {
    list.innerHTML = '<div class="empty-state">No chats yet.<br>Your conversations appear here.</div>';
    return;
  }
  list.replaceChildren(...sessions.map(s => {
    const row = document.createElement('div');
    row.className = 'chat-row' + (s.id === state.sessionId ? ' active' : '');
    row.title = s.title || 'Untitled chat';

    const title = document.createElement('span');
    title.className = 'chat-title';
    title.textContent = s.title || 'Untitled chat';

    const time = document.createElement('span');
    time.className = 'chat-time';
    time.textContent = s.id === state.sessionId ? 'now' : fmtChatTime(s.updated_at);

    row.appendChild(title);
    row.appendChild(time);
    row.addEventListener('click', () => reopenChat(s.id));
    return row;
  }));
}

async function reopenChat(sessionId) {
  if (state.isStreaming || sessionId === state.sessionId) return;
  try {
    const resp = await fetch(`/v1/sessions/${state.userId}/${sessionId}/history`);
    if (!resp.ok) return;
    const history = await resp.json();
    state.sessionId = sessionId;
    localStorage.setItem('victoria_session', sessionId);
    messagesEl.innerHTML = '';
    hideWelcome();
    history.forEach(m => appendMessage(m.role === 'user' ? 'user' : 'assistant', m.content));
    sessionDisplay.textContent = `SESSION: ${sessionId.slice(0, 8).toUpperCase()}`;
    loadSidebarData();   // re-mark the active topic
  } catch (_) {}
}

// ── First-run onboarding ───────────────────────────────────────
function showOnboarding(prefillName) {
  const overlay = document.getElementById('onboard-overlay');
  if (!overlay || !overlay.classList.contains('hidden')) return;   // already shown
  const nameEl = document.getElementById('onboard-name');
  if (prefillName) nameEl.value = prefillName;
  overlay.classList.remove('hidden');
  setTimeout(() => nameEl.focus(), 50);
}

async function submitOnboarding() {
  const btn = document.getElementById('onboard-submit');
  const name    = document.getElementById('onboard-name').value.trim();
  const address = document.getElementById('onboard-address').value.trim();
  btn.disabled = true;
  try {
    await fetch(`/v1/profile/${state.userId}/onboard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, preferred_address: address }),
    });
  } catch (_) {}
  document.getElementById('onboard-overlay').classList.add('hidden');
  btn.disabled = false;
  loadSidebarData();
}

document.getElementById('onboard-submit').addEventListener('click', submitOnboarding);
document.getElementById('onboard-overlay').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); submitOnboarding(); }
});

// ── Helpers ────────────────────────────────────────────────────
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideWelcome() {
  if (welcome) welcome.style.display = 'none';
}

function timestamp() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

// ── Message rendering ──────────────────────────────────────────
function appendMessage(role, text) {
  hideWelcome();
  const row = document.createElement('div');
  row.className = `msg-row ${role}`;

  const meta = document.createElement('div');
  meta.className = 'msg-meta';

  const label = document.createElement('span');
  label.className = role === 'user' ? 'msg-label-user' : 'msg-label-vic';
  label.textContent = role === 'user' ? 'OPERATOR' : 'VICTORIA';

  const time = document.createElement('span');
  time.className = 'msg-time';
  time.textContent = timestamp();

  meta.appendChild(label);
  meta.appendChild(time);

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;

  row.appendChild(meta);
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();

  return { row, bubble, meta };
}

// ── Send message ───────────────────────────────────────────────
async function sendMessage(text) {
  text = text.trim();
  if (!text || state.isStreaming) return;

  setStreaming(true);
  appendMessage('user', text);

  const { bubble, meta } = appendMessage('assistant', '');
  bubble.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';

  let accumulated = '';
  let firstChunk  = true;

  try {
    const resp = await fetch('/v1/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message:    text,
        session_id: state.sessionId,
        user_id:    state.userId,
        backend:    backendSel.value || null,
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch { continue; }

        if (!state.sessionId && data.session_id) {
          state.sessionId = data.session_id;
          localStorage.setItem('victoria_session', state.sessionId);
          sessionDisplay.textContent = `SESSION: ${state.sessionId.slice(0, 8).toUpperCase()}`;
        }

        if (data.done) {
          bubble.classList.remove('streaming');
          // Update sidebar backend display — include the local model when known,
          // so routing (qwen2.5 for chat/tools vs qwen3-coder for code) is visible.
          const backendLabel = (data.backend || '—').toUpperCase();
          const shortModel = data.model ? data.model.split('/').pop() : '';
          const label = shortModel ? `${backendLabel} · ${shortModel}` : backendLabel;
          document.getElementById('sys-backend').textContent  = label;
          document.getElementById('last-backend').textContent = label;
          document.getElementById('last-status').textContent  = 'OK';

          // Add backend badge to meta
          const badge = document.createElement('span');
          badge.className = 'msg-backend';
          badge.textContent = `[${label}]`;
          meta.appendChild(badge);

          // Speak the reply aloud when spoken-replies is on, or when the
          // query came in by voice. Track the promise so hands-free mode can
          // wait for her to finish before listening again.
          if (state.speakReplies || state.speakNext) {
            state.speakPromise = speakText(accumulated);
          } else {
            state.speakPromise = Promise.resolve();
          }
          state.speakNext = false;

          // Refresh sidebar data (profile may have been updated)
          setTimeout(loadSidebarData, 2000);
        } else if (data.chunk) {
          if (firstChunk) {
            bubble.textContent = '';
            bubble.classList.add('streaming');
            firstChunk = false;
          }
          accumulated += data.chunk;
          bubble.textContent = accumulated;
          scrollToBottom();
        }
      }
    }
  } catch (err) {
    bubble.classList.remove('streaming');
    bubble.textContent = 'TRANSMISSION ERROR — Please retry.';
    bubble.style.borderLeftColor = 'var(--red)';
    document.getElementById('last-status').textContent = 'ERROR';
    console.error(err);
  } finally {
    setStreaming(false);
  }
}

// ── New session ────────────────────────────────────────────────
function newSession() {
  state.sessionId = null;
  localStorage.removeItem('victoria_session');
  messagesEl.innerHTML = '';
  if (welcome) welcome.style.display = '';
  sessionDisplay.textContent = 'SESSION: —';
  document.getElementById('last-backend').textContent = '—';
  document.getElementById('last-status').textContent  = '—';
  setStatus('SYSTEM NOMINAL', '');
  loadSidebarData();   // the chat that just ended now appears under Topics
}

// ── Auto-resize input ──────────────────────────────────────────
function resizeInput() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + 'px';
}

// ── Local model selector (Docker Model Runner, switch at runtime) ─
function shortModelName(id) {
  return id.replace(/^docker\.io\//, '').replace(/^ai\//, '');
}

async function loadModels() {
  if (!modelSelect) return;
  try {
    const resp = await fetch('/v1/models');
    if (!resp.ok) return;
    const data = await resp.json();
    const models = data.models || [];
    if (!models.length) {
      modelSelect.innerHTML = '<option value="">no models pulled</option>';
      if (modelHint) modelHint.textContent = 'Pull one: docker model pull ai/qwen2.5';
      return;
    }
    modelSelect.innerHTML = '';
    for (const m of models) {
      const opt = document.createElement('option');
      opt.value = m.id;
      const bits = [shortModelName(m.id)];
      if (m.params) bits.push(String(m.params).trim());
      if (m.context) bits.push(Math.round(m.context / 1000) + 'k ctx');
      if (m.id === data.recommended) bits.push('★');
      opt.textContent = bits.join(' · ');
      if (m.id === data.active) opt.selected = true;
      modelSelect.appendChild(opt);
    }
    if (modelHint) {
      const rec = data.recommended ? shortModelName(data.recommended) : '—';
      modelHint.textContent = `RAM ${data.ram_gb || '?'} GB · ★ recommended: ${rec}`;
    }
  } catch (err) { console.error('models load', err); }
}

async function selectModel() {
  const model = modelSelect.value;
  if (!model) return;
  try {
    const resp = await fetch('/v1/models/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (!resp.ok) { setStatus('MODEL SWITCH FAILED', 'var(--red)'); return; }
    setStatus('MODEL → ' + shortModelName(model), 'var(--teal-bright)');
  } catch (err) { console.error('model select', err); setStatus('MODEL SWITCH FAILED', 'var(--red)'); }
}

if (modelSelect) {
  modelSelect.addEventListener('change', selectModel);
  loadModels();
}

// ── Credentials vault (names only; values never leave the vault) ─
async function loadVault() {
  if (!vaultList) return;
  try {
    const resp = await fetch('/v1/vault');
    if (!resp.ok) return;
    const names = (await resp.json()).names || [];
    vaultCount.textContent = names.length;
    if (!names.length) {
      vaultList.innerHTML = '<div class="empty-state">No secrets stored.</div>';
      return;
    }
    vaultList.innerHTML = '';
    for (const name of names) {
      const row = document.createElement('div');
      row.className = 'vault-item';
      const label = document.createElement('span');
      label.textContent = name;                    // name only — never a value
      const del = document.createElement('button');
      del.className = 'vault-del';
      del.textContent = '✕';
      del.title = `Delete ${name}`;
      del.addEventListener('click', () => deleteSecret(name));
      row.appendChild(label);
      row.appendChild(del);
      vaultList.appendChild(row);
    }
  } catch (err) { console.error('vault load', err); }
}

async function storeSecret() {
  const name = (vaultName.value || '').trim();
  const value = vaultValue.value || '';
  if (!name || !value) { setStatus('VAULT: NAME + VALUE REQUIRED', 'var(--red)'); return; }
  try {
    const resp = await fetch('/v1/vault', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, value }),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    vaultName.value = '';
    vaultValue.value = '';        // never retained in the UI
    setStatus('VAULT: STORED ' + name, 'var(--teal-bright)');
    loadVault();
  } catch (err) { console.error('vault store', err); setStatus('VAULT: STORE FAILED', 'var(--red)'); }
}

async function deleteSecret(name) {
  try {
    await fetch('/v1/vault/' + encodeURIComponent(name), { method: 'DELETE' });
    loadVault();
  } catch (err) { console.error('vault delete', err); }
}

if (vaultStoreBtn) {
  vaultStoreBtn.addEventListener('click', storeSecret);
  vaultValue.addEventListener('keydown', e => { if (e.key === 'Enter') storeSecret(); });
  loadVault();
}

// ── Voice: speech-to-text (mic) + text-to-speech (playback) ─────
let mediaRecorder = null;
let audioChunks   = [];
let currentAudio  = null;

function updateSpeakBtn() {
  speakBtn.classList.toggle('active', state.speakReplies);
  speakBtn.title = state.speakReplies ? 'Spoken replies: ON' : 'Spoken replies: OFF';
}

// Returns a promise that resolves when playback finishes (so hands-free mode
// can wait for Victoria to stop talking before it listens again).
async function speakText(text) {
  if (!text || !text.trim()) return;
  let blob;
  try {
    const resp = await fetch('/v1/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) { console.error('TTS HTTP', resp.status); return; }
    blob = await resp.blob();
  } catch (err) {
    console.error('TTS error', err);
    return;
  }
  if (currentAudio) { currentAudio.pause(); }
  currentAudio = new Audio(URL.createObjectURL(blob));
  await new Promise(resolve => {
    currentAudio.addEventListener('ended', resolve, { once: true });
    currentAudio.addEventListener('error', resolve, { once: true });
    currentAudio.play().catch(err => { console.error('audio play failed', err); resolve(); });
  });
}

// Transcribe an audio blob and send it. Returns true if a question was sent.
// Awaits the full turn (send → reply streamed → spoken) so callers know when
// the whole exchange is done.
async function transcribeAndSend(blob) {
  setStatus('TRANSCRIBING', 'var(--teal-bright)');
  const ext = blob.type.includes('ogg') ? 'ogg'
            : blob.type.includes('mp4') ? 'mp4'
            : blob.type.includes('wav') ? 'wav' : 'webm';
  const fd = new FormData();
  fd.append('audio', blob, `speech.${ext}`);
  try {
    const resp = await fetch('/v1/transcribe', { method: 'POST', body: fd });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const text = (data.text || '').trim();
    if (!text) { setStatus('NO SPEECH HEARD', 'var(--red)'); return false; }
    // Echo what was heard so it's visible even before the bubble renders.
    inputEl.value = text;
    resizeInput();
    state.speakNext = true;        // heard by voice → reply by voice
    state.speakPromise = Promise.resolve();
    await sendMessage(text);
    inputEl.value = '';            // the user bubble now shows the text
    resizeInput();
    await (state.speakPromise || Promise.resolve());  // wait for her to finish speaking
    return true;
  } catch (err) {
    console.error('transcribe error', err);
    setStatus('STT ERROR', 'var(--red)');
    return false;
  }
}

async function toggleRecording() {
  if (state.isRecording) { mediaRecorder && mediaRecorder.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    setStatus('MIC UNSUPPORTED', 'var(--red)');
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = e => { if (e.data.size) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      state.isRecording = false;
      micBtn.classList.remove('recording');
      const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      if (blob.size) await transcribeAndSend(blob);
    };
    mediaRecorder.start();
    state.isRecording = true;
    micBtn.classList.add('recording');
    setStatus('LISTENING', 'var(--teal-bright)');
  } catch (err) {
    console.error('mic error', err);
    setStatus('MIC BLOCKED', 'var(--red)');
  }
}

// ── Hands-free wake-word mode ("Victoria") ─────────────────────
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const HANDS_FREE_SUPPORTED = !!SR && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
let recognition = null;
let hfStream = null;

function setMicVisual() {
  micBtn.classList.toggle('armed', state.handsFree && state.hfPhase === 'listening');
  micBtn.classList.toggle('recording', state.hfPhase === 'capturing' || state.isRecording);
  if (state.handsFree) {
    micBtn.title = 'Hands-free ON — say "Victoria" (click to stop)';
  } else {
    micBtn.title = HANDS_FREE_SUPPORTED
      ? 'Click for hands-free (say "Victoria")'
      : 'Hold to speak (click to start/stop)';
  }
}

function startRecognition() {
  if (!state.handsFree || !recognition) return;
  try { recognition.start(); } catch (e) { /* already running */ }
}

function beginListening() {
  if (!state.handsFree) return;
  state.hfPhase = 'listening';
  setMicVisual();
  setStatus('SAY “VICTORIA”', 'var(--teal)');
  startRecognition();
}

async function toggleHandsFree() {
  if (state.handsFree) { stopHandsFree(); return; }
  try {
    hfStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    console.error('mic error', err);
    setStatus('MIC BLOCKED', 'var(--red)');
    return;
  }
  recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = 'en-GB';
  recognition.onresult = (ev) => {
    if (state.hfPhase !== 'listening') return;
    let text = '';
    for (let i = ev.resultIndex; i < ev.results.length; i++) text += ev.results[i][0].transcript;
    if (text.toLowerCase().includes(WAKE_WORD)) captureQuestion();
  };
  recognition.onerror = (ev) => {
    if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
      setStatus('MIC BLOCKED', 'var(--red)');
      stopHandsFree();
    }
  };
  recognition.onend = () => {
    // Chrome ends recognition periodically — restart while still listening.
    if (state.handsFree && state.hfPhase === 'listening') startRecognition();
  };
  state.handsFree = true;
  beginListening();
}

function stopHandsFree() {
  state.handsFree = false;
  state.hfPhase = 'idle';
  if (recognition) { recognition.onend = null; try { recognition.stop(); } catch (e) {} recognition = null; }
  if (hfStream) { hfStream.getTracks().forEach(t => t.stop()); hfStream = null; }
  micBtn.classList.remove('armed', 'recording');
  setMicVisual();
  setStatus('SYSTEM NOMINAL', '');
}

function resumeListening() {
  if (!state.handsFree) return;
  // brief cooldown so the tail of her spoken reply isn't picked up as input
  setTimeout(beginListening, 400);
}

async function captureQuestion() {
  state.hfPhase = 'capturing';
  setMicVisual();
  try { recognition && recognition.stop(); } catch (e) {}   // pause wake listening
  setStatus('LISTENING…', 'var(--teal-bright)');

  let recorder;
  try { recorder = new MediaRecorder(hfStream); }
  catch (err) { console.error('recorder error', err); return resumeListening(); }

  const chunks = [];
  recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
  const stopMonitor = monitorSilence(hfStream, () => {
    if (recorder.state === 'recording') recorder.stop();
  });
  recorder.onstop = async () => {
    stopMonitor();
    state.hfPhase = 'processing';
    setMicVisual();
    setStatus('THINKING…', 'var(--teal-bright)');
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (blob.size) await transcribeAndSend(blob);
    resumeListening();
  };
  recorder.start();
}

// Calls onSilence after ~1.2s of quiet following detected speech, or a hard
// cap; also bails if no speech at all within 6s. Returns a stop() fn.
function monitorSilence(stream, onSilence) {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);
  const started = performance.now();
  let hasSpoken = false, lastLoud = performance.now(), raf = 0, done = false;

  function finish() {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf);
    ctx.close().catch(() => {});
    onSilence();
  }
  function tick() {
    if (done) return;
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now();
    if (rms > 0.04) { hasSpoken = true; lastLoud = now; }
    if ((hasSpoken && now - lastLoud > 1200) || now - started > 12000 ||
        (!hasSpoken && now - started > 6000)) { finish(); return; }
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);
  return finish;
}

micBtn.addEventListener('click', () => {
  if (HANDS_FREE_SUPPORTED) toggleHandsFree();
  else toggleRecording();          // fallback: manual push-to-talk
});
setMicVisual();

speakBtn.addEventListener('click', () => {
  state.speakReplies = !state.speakReplies;
  localStorage.setItem('victoria_speak', state.speakReplies ? '1' : '');
  if (!state.speakReplies && currentAudio) currentAudio.pause();
  updateSpeakBtn();
});
updateSpeakBtn();

// ── Events ─────────────────────────────────────────────────────
inputEl.addEventListener('input', resizeInput);

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = inputEl.value;
    inputEl.value = '';
    resizeInput();
    sendMessage(text);
  }
});

sendBtn.addEventListener('click', () => {
  const text = inputEl.value;
  inputEl.value = '';
  resizeInput();
  sendMessage(text);
});

newSessionBtn.addEventListener('click', newSession);

document.querySelectorAll('.hint-chip').forEach(chip => {
  chip.addEventListener('click', () => sendMessage(chip.dataset.prompt));
});

// ── Init ───────────────────────────────────────────────────────
if (state.sessionId) {
  sessionDisplay.textContent = `SESSION: ${state.sessionId.slice(0, 8).toUpperCase()}`;
}
inputEl.focus();

// ── Avatar (Tier 0 procedural face) ────────────────────────────
// A zero-asset stylized face in the bottom-left: calm when idle, alert green
// when listening, amber + thought-dots when thinking, and an open mouth +
// equaliser driven by the live TTS amplitude when speaking. Swappable for a
// Rive/Live2D/3D rig later, behind the same state model.
(function initAvatar() {
  const dock = document.getElementById('avatar-dock');
  if (!dock) return;
  const stateEl = document.getElementById('av-state');
  const mouth = document.getElementById('av-mouth');
  const bars = [0, 1, 2, 3, 4].map(i => document.getElementById('av-bar-' + i));
  const barBase = bars.map(b => parseFloat(b.getAttribute('height')));
  const barBottom = bars.map(b => parseFloat(b.getAttribute('y')) + parseFloat(b.getAttribute('height')));
  const barFactor = [0.6, 0.85, 1, 0.85, 0.6];
  const MOUTH_RY = mouth ? parseFloat(mouth.getAttribute('ry')) : 6;
  const LABEL = { idle: 'IDLE', listening: 'LISTENING', thinking: 'THINKING', speaking: 'SPEAKING' };

  let audioCtx = null, analyser = null, freq = null, wiredEl = null;
  let level = 0, tPrev = 0;

  function isSpeaking() {
    return currentAudio && !currentAudio.paused && !currentAudio.ended;
  }

  function deriveState() {
    if (isSpeaking()) return 'speaking';
    if (state.isStreaming || state.hfPhase === 'processing') return 'thinking';
    if (state.hfPhase === 'listening' || state.hfPhase === 'capturing' || state.isRecording) return 'listening';
    return 'idle';
  }

  // Attach an analyser to the current TTS <audio>, once per element. Any failure
  // (autoplay policy, one-source-per-element, …) falls back to a synthetic mouth
  // movement so the avatar still animates while speaking.
  function wireAudio() {
    if (!currentAudio || currentAudio === wiredEl) return;
    wiredEl = currentAudio;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      audioCtx = audioCtx || new AC();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const src = audioCtx.createMediaElementSource(currentAudio);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      freq = new Uint8Array(analyser.fftSize);
      src.connect(analyser);
      analyser.connect(audioCtx.destination);
    } catch (e) {
      analyser = null;
    }
  }

  function frame(t) {
    const dt = Math.min(0.05, (t - tPrev) / 1000) || 0;
    tPrev = t;
    const st = deriveState();
    if (dock.getAttribute('data-avstate') !== st) {
      dock.setAttribute('data-avstate', st);
      stateEl.textContent = LABEL[st];
    }

    let target = 0;
    if (st === 'speaking') {
      wireAudio();
      if (analyser) {
        analyser.getByteTimeDomainData(freq);
        let sum = 0;
        for (let i = 0; i < freq.length; i++) { const v = (freq[i] - 128) / 128; sum += v * v; }
        target = Math.min(1, Math.sqrt(sum / freq.length) * 3.4);
      } else {
        target = 0.4 + 0.35 * Math.abs(Math.sin(t * 0.011)) + 0.15 * Math.random();
      }
    }

    level += (target - level) * Math.min(1, dt * 16);
    if (level < 0.001) level = 0;

    if (st === 'speaking') {
      for (let i = 0; i < bars.length; i++) {
        const h = barBase[i] + level * 22 * barFactor[i];
        bars[i].setAttribute('height', h.toFixed(1));
        bars[i].setAttribute('y', (barBottom[i] - h).toFixed(1));
      }
      if (mouth) mouth.setAttribute('ry', (MOUTH_RY + level * 5).toFixed(1));
    }

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
