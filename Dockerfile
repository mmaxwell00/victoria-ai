FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# System tools the app shells out to:
#   - nodejs/npm  → the Claude Code CLI (escalation) + npx-based MCP servers
#   - git         → GitHub skill import
#   - ffmpeg      → Whisper speech-to-text for browser voice (/v1/transcribe)
# Escalation auth is via CLAUDE_CODE_OAUTH_TOKEN (injected at runtime, see
# setup-victoria-mac.sh); without it, escalation is simply disabled.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates git ffmpeg \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY victoria/ victoria/
# scripts/ is needed by the victoria-telegram compose service
COPY scripts/ scripts/

RUN mkdir -p data models

CMD ["uvicorn", "victoria.main:app", "--host", "0.0.0.0", "--port", "8000"]
