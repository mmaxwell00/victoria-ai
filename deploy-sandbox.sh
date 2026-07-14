#!/bin/bash
# Victoria Sandbox Deployment Checklist & Setup
# Run this before: sbx run --kit ./sbx-kit.yaml

set -e

echo "=== Victoria Sandbox Deployment Checklist ==="
echo

# 1. Verify sbx CLI installed
echo "[1/7] Checking sbx CLI..."
if ! command -v sbx &> /dev/null; then
  echo "❌ sbx CLI not found. Install it:"
  echo "   macOS: brew install docker/tap/sbx"
  echo "   Windows: winget install Docker.sbx"
  exit 1
fi
SBX_VERSION=$(sbx --version 2>&1 | head -1)
echo "✓ sbx CLI found: $SBX_VERSION"
echo

# 2. Verify kit file exists and is valid YAML
echo "[2/7] Checking kit file..."
if [ ! -f ./sbx-kit.yaml ]; then
  echo "❌ sbx-kit.yaml not found in current directory"
  exit 1
fi
echo "✓ sbx-kit.yaml found"

# Validate YAML syntax if yamllint available
if command -v yamllint &> /dev/null; then
  if yamllint -d relaxed ./sbx-kit.yaml > /dev/null 2>&1; then
    echo "✓ Kit YAML syntax valid"
  else
    echo "⚠️  Kit YAML has syntax issues (non-fatal; may still work)"
  fi
fi
echo

# 3. Verify Docker Desktop and Model Runner running
echo "[3/7] Checking Docker Desktop and Model Runner..."
if ! docker ps &> /dev/null; then
  echo "❌ Docker daemon not running. Start Docker Desktop."
  exit 1
fi
echo "✓ Docker daemon running"

# Check if Model Runner is accessible on host
if curl -s http://localhost:12434/engines/llama.cpp/v1/models &> /dev/null; then
  echo "✓ Model Runner accessible at localhost:12434"
else
  echo "⚠️  Model Runner not accessible at localhost:12434"
  echo "    Enable in Docker Desktop → Settings → Features in development → Docker Model Runner"
  echo "    Then run: docker desktop enable model-runner --tcp=12434"
fi
echo

# 4. Verify model is pulled
echo "[4/7] Checking for pulled models..."
if docker model ls &> /dev/null; then
  MODELS=$(docker model ls | tail -n +2 | wc -l)
  if [ "$MODELS" -eq 0 ]; then
    echo "⚠️  No models pulled. Pull one before running:"
    echo "    docker model pull ai/qwen2.5"
    echo "    docker model pull ai/llama3.2"
  else
    echo "✓ $MODELS model(s) found:"
    docker model ls | sed 's/^/   /'
  fi
else
  echo "⚠️  Cannot list models. Model Runner may not be enabled."
fi
echo

# 5. Set up local sandbox policy (CRITICAL)
echo "[5/7] Setting up local sbx policy..."
if command -v sbx &> /dev/null; then
  # Check if policy already exists
  if sbx policy list 2>/dev/null | grep -q "localhost:12434"; then
    echo "✓ localhost:12434 policy already exists"
  else
    echo "→ Creating policy: sbx policy allow network localhost:12434"
    if sbx policy allow network localhost:12434 > /dev/null 2>&1; then
      echo "✓ Policy created"
    else
      echo "⚠️  Policy creation may have failed (check sbx policy list)"
    fi
  fi
  
  # Show all active policies
  echo "  Active policies:"
  sbx policy list | sed 's/^/    /'
else
  echo "❌ sbx CLI not found (checked in step 1)"
  exit 1
fi
echo

# 6. Verify .env or provide guidance
echo "[6/7] Checking environment setup..."
if [ ! -f ./.env ]; then
  echo "ℹ️  .env not found — kit will use defaults"
  echo "    (Kit env vars override .env anyway)"
else
  echo "✓ .env found"
  # Check if key secrets are set (without exposing them)
  if grep -q "ANTHROPIC_API_KEY" .env; then
    echo "  ✓ ANTHROPIC_API_KEY appears to be set (for Claude escalation)"
  fi
  if grep -q "TELEGRAM_BOT_TOKEN" .env; then
    TELEGRAM_SET=$(grep "TELEGRAM_BOT_TOKEN=" .env | grep -v "^#" | grep -v "=$")
    if [ ! -z "$TELEGRAM_SET" ]; then
      echo "  ✓ TELEGRAM_BOT_TOKEN appears to be set"
    fi
  fi
fi
echo

# 7. Summary and next steps
echo "[7/7] Pre-flight checks complete"
echo

echo "=== Ready to Deploy ==="
echo
echo "Next: Launch the sandbox"
echo "  sbx run --kit ./sbx-kit.yaml"
echo
echo "This will:"
echo "  1. Create a new sandbox microVM"
echo "  2. Install Python, ffmpeg, Node.js, and all dependencies"
echo "  3. Download the Piper voice model"
echo "  4. Start Victoria API on :8000"
echo
echo "Inside the sandbox, verify:"
echo "  • Browser: http://localhost:8000"
echo "  • Model Runner: curl http://localhost:12434/engines/llama.cpp/v1/models"
echo "  • API: curl -X POST http://localhost:8000/v1/chat -H 'Content-Type: application/json' -d '{\"message\":\"hi\",\"user_id\":\"test\"}'"
echo
echo "Troubleshooting:"
echo "  • View sandbox logs: sbx logs <sandbox-id>"
echo "  • List sandboxes: sbx list"
echo "  • Stop sandbox: sbx stop <sandbox-id>"
echo "  • Check policies: sbx policy list"
echo
echo "Optional: Add more network access"
echo "  • ElevenLabs TTS: sbx policy allow network api.elevenlabs.io"
echo "  • Telegram: sbx policy allow network api.telegram.org"
echo
