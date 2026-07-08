FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Claude Code CLI — lets the local-first escalation feature work inside the
# container. Auth via CLAUDE_CODE_OAUTH_TOKEN in .env (see setup-victoria-mac.sh);
# without a token, escalation is simply disabled and nothing else is affected.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY victoria/ victoria/
# scripts/ is needed by the victoria-telegram compose service
COPY scripts/ scripts/

RUN mkdir -p data models

CMD ["uvicorn", "victoria.main:app", "--host", "0.0.0.0", "--port", "8000"]
