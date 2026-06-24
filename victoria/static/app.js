'use strict';

// ── State ──────────────────────────────────────────────────────
const state = {
  sessionId: localStorage.getItem('victoria_session') || null,
  isStreaming: false,
  userId: 'web',
};

// ── DOM refs ───────────────────────────────────────────────────
const messagesEl    = document.getElementById('chat-messages');
const welcome       = document.getElementById('welcome');
const inputEl       = document.getElementById('message-input');
const sendBtn       = document.getElementById('send-btn');
const backendSel    = document.getElementById('backend-select');
const newSessionBtn = document.getElementById('new-session-btn');
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
        document.getElementById('p-style').textContent   = p.communication_style || '—';
        document.getElementById('p-topics').textContent  = p.topics_of_interest?.join(', ') || '—';
        document.getElementById('p-pref-count').textContent = p.preferences?.length || '0';

        const memList = document.getElementById('memories-list');
        document.getElementById('mem-count').textContent = p.explicit_memories?.length || '0';
        if (p.explicit_memories?.length > 0) {
          memList.innerHTML = p.explicit_memories
            .map(m => `<div class="memory-item">${m}</div>`)
            .join('');
        }
      }
    }
  } catch (_) {}

  // Sessions
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
          // Update sidebar backend display
          const backendLabel = (data.backend || '—').toUpperCase();
          document.getElementById('sys-backend').textContent  = backendLabel;
          document.getElementById('last-backend').textContent = backendLabel;
          document.getElementById('last-status').textContent  = 'OK';

          // Add backend badge to meta
          const badge = document.createElement('span');
          badge.className = 'msg-backend';
          badge.textContent = `[${backendLabel}]`;
          meta.appendChild(badge);

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
}

// ── Auto-resize input ──────────────────────────────────────────
function resizeInput() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + 'px';
}

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
