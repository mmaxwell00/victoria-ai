'use strict';

// ── State ──────────────────────────────────────────────────────
const state = {
  sessionId: localStorage.getItem('victoria_session') || null,
  isStreaming: false,
};

// ── DOM refs ───────────────────────────────────────────────────
const chatWindow   = document.getElementById('chat-window');
const messagesEl   = document.getElementById('messages');
const welcome      = document.getElementById('welcome');
const inputEl      = document.getElementById('message-input');
const sendBtn      = document.getElementById('send-btn');
const backendSel   = document.getElementById('backend-select');
const newSessionBtn= document.getElementById('new-session-btn');
const statusLine   = document.getElementById('status-line');

// ── Helpers ────────────────────────────────────────────────────
function scrollToBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function setStatus(text) {
  statusLine.textContent = text;
}

function setStreaming(on) {
  state.isStreaming = on;
  sendBtn.disabled = on;
  inputEl.disabled = on;
  setStatus(on ? 'Thinking…' : 'Ready');
}

function hideWelcome() {
  if (!welcome.classList.contains('hidden')) {
    welcome.style.display = 'none';
  }
}

// ── Message rendering ──────────────────────────────────────────
function appendMessage(role, text) {
  hideWelcome();

  const row = document.createElement('div');
  row.className = `msg-row ${role}`;

  if (role === 'assistant') {
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = 'V';
    row.appendChild(avatar);
  }

  const body = document.createElement('div');
  body.className = 'msg-body';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  body.appendChild(bubble);

  row.appendChild(body);
  messagesEl.appendChild(row);
  scrollToBottom();

  return { row, bubble, body };
}

function showTyping() {
  const { row, bubble, body } = appendMessage('assistant', '');
  bubble.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
  return { row, bubble, body };
}

function addBackendBadge(body, backend) {
  const badge = document.createElement('span');
  badge.className = `backend-badge badge-${backend}`;
  badge.textContent = `● ${backend}`;
  body.appendChild(badge);
}

// ── Send message ───────────────────────────────────────────────
async function sendMessage(text) {
  text = text.trim();
  if (!text || state.isStreaming) return;

  setStreaming(true);
  appendMessage('user', text);

  const { bubble, body } = showTyping();
  let firstChunk = true;
  let accumulated = '';

  try {
    const resp = await fetch('/v1/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        session_id: state.sessionId,
        user_id: 'web',
        backend: backendSel.value || null,
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch { continue; }

        // Capture session ID on first response
        if (!state.sessionId && data.session_id) {
          state.sessionId = data.session_id;
          localStorage.setItem('victoria_session', state.sessionId);
        }

        if (data.done) {
          bubble.classList.remove('streaming-cursor');
          addBackendBadge(body, data.backend);
        } else if (data.chunk) {
          if (firstChunk) {
            bubble.textContent = '';
            bubble.classList.add('streaming-cursor');
            firstChunk = false;
          }
          accumulated += data.chunk;
          bubble.textContent = accumulated;
          scrollToBottom();
        }
      }
    }
  } catch (err) {
    bubble.classList.remove('streaming-cursor');
    bubble.textContent = 'Terribly sorry — something went sideways on my end. Do try again.';
    console.error('Stream error:', err);
  } finally {
    setStreaming(false);
    scrollToBottom();
  }
}

// ── New session ────────────────────────────────────────────────
function newSession() {
  state.sessionId = null;
  localStorage.removeItem('victoria_session');
  messagesEl.innerHTML = '';
  welcome.style.display = '';
  setStatus('Ready');
}

// ── Auto-resize textarea ───────────────────────────────────────
function resizeInput() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
}

// ── Event listeners ────────────────────────────────────────────
inputEl.addEventListener('input', resizeInput);

inputEl.addEventListener('keydown', (e) => {
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

// Hint chips
document.querySelectorAll('.hint-chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    const prompt = chip.dataset.prompt;
    sendMessage(prompt);
  });
});

// Focus input on load
inputEl.focus();
