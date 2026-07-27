#!/usr/bin/env python3
"""Victoria ↔ Claude host bridge.

Runs on the HOST (not in a sandbox). Victoria's sandbox POSTs a prompt here over
mutual TLS; the bridge runs `claude -p` inside a governed built-in claude-agent
sandbox (via SSH) and returns the answer. The Claude subscription credential stays
on the host (sbx proxy) and never enters Victoria's VM — only the prompt leaves it,
only the answer returns. See docs/claude-bridge-architecture.svg.

Design goals:
  * stdlib only (runs with the host's system python3 — no venv, no pip),
  * mutual TLS: the bridge presents a server cert AND requires a client cert
    signed by our CA, so only Victoria (holding that client cert) can call it,
  * the untrusted prompt is passed on the child's STDIN (never as a shell arg),
    so it can't be reinterpreted by a shell.

Usage:
  # 1. One-time: generate a CA + server cert + client cert (openssl)
  ./claude-bridge.py --gen-certs ~/.victoria/bridge-certs

  # 2. Run the bridge (serves https://<bind>:<port>/ask)
  CLAUDE_SANDBOX=victoria-claude ./claude-bridge.py --certs ~/.victoria/bridge-certs

  # 3. Point Victoria at it (in the sandbox env / sbx spec):
  #   CLAUDE_BRIDGE_URL=https://host.docker.internal:8787/ask
  #   CLAUDE_BRIDGE_CLIENT_CERT=/…/client.crt  CLAUDE_BRIDGE_CLIENT_KEY=/…/client.key
  #   CLAUDE_BRIDGE_CA_CERT=/…/ca.crt

Environment (serve mode):
  BRIDGE_BIND        interface to bind      (default 0.0.0.0 — mTLS is the gate)
  BRIDGE_PORT        port                   (default 8787)
  CLAUDE_SANDBOX     sbx sandbox name       (default victoria-claude)
  CLAUDE_SSH_HOST    ssh target             (default $CLAUDE_SANDBOX.sbx)
  CLAUDE_BIN         remote claude binary   (default claude)
  CLAUDE_TIMEOUT     per-call seconds       (default 120)
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- input guards ---------------------------------------------------------- #
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_TOOL_RE = re.compile(r"^[A-Za-z]{1,32}$")
_MAX_PROMPT = 100_000
_MAX_SYSTEM = 20_000


def _ssh_host() -> str:
    return os.environ.get("CLAUDE_SSH_HOST") or f"{os.environ.get('CLAUDE_SANDBOX', 'victoria-claude')}.sbx"


def _run_claude(prompt: str, system_prompt: str, model: str, allowed_tools: str) -> str:
    """Run `claude -p` in the governed sandbox over SSH. Prompt goes on STDIN
    (never a shell arg); model/tools/system are validated before use."""
    if not _MODEL_RE.match(model or ""):
        model = "sonnet"
    remote = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--model", model]
    if system_prompt:
        remote += ["--append-system-prompt", system_prompt[:_MAX_SYSTEM]]
    tools = [t for t in (allowed_tools or "").replace(",", " ").split() if _TOOL_RE.match(t)]
    if tools:
        remote += ["--allowedTools", *tools]
    # `ssh host -- <argv>` : ssh joins the remote argv with spaces and the remote
    # shell re-parses it. We only place validated tokens (model, fixed flags,
    # regex-checked tool names, and Victoria's own system prompt) in the argv; the
    # untrusted user prompt is fed on stdin, so it can never become a remote arg.
    argv = ["ssh", "-o", "BatchMode=yes", _ssh_host(), "--", *remote]
    timeout = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    proc = subprocess.run(argv, input=prompt[:_MAX_PROMPT], capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output").strip()[:500]
        raise RuntimeError(f"claude exited {proc.returncode}: {detail}")
    return (proc.stdout or "").strip()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/ask":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._json(400, {"error": "invalid JSON"})
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            return self._json(400, {"error": "no prompt"})
        try:
            answer = _run_claude(prompt, req.get("system_prompt", ""),
                                 req.get("model", "sonnet"), req.get("allowed_tools", ""))
            self._json(200, {"answer": answer})
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "claude timed out"})
        except Exception as exc:  # noqa: BLE001 — surface a clean error to Victoria
            self._json(502, {"error": str(exc)[:500]})

    def log_message(self, fmt, *args):  # quieter, and never logs the prompt body
        sys.stderr.write("[bridge] %s - %s\n" % (self.client_address[0], fmt % args))


def _serve(certs_dir: str) -> None:
    ca = os.path.join(certs_dir, "ca.crt")
    crt = os.path.join(certs_dir, "server.crt")
    key = os.path.join(certs_dir, "server.key")
    for f in (ca, crt, key):
        if not os.path.exists(f):
            sys.exit(f"missing {f} — run: {sys.argv[0]} --gen-certs {certs_dir}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=crt, keyfile=key)
    ctx.load_verify_locations(cafile=ca)
    ctx.verify_mode = ssl.CERT_REQUIRED          # ← mutual TLS: client cert required
    bind = os.environ.get("BRIDGE_BIND", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", "8787"))
    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write(f"[bridge] mTLS listening on https://{bind}:{port}/ask → ssh {_ssh_host()} claude -p\n")
    httpd.serve_forever()


def _gen_certs(certs_dir: str) -> None:
    """Generate a CA + server cert (SAN host.docker.internal) + client cert."""
    os.makedirs(certs_dir, exist_ok=True)
    j = lambda n: os.path.join(certs_dir, n)  # noqa: E731
    def sh(*a):
        subprocess.run(a, check=True)
    sh("openssl", "genrsa", "-out", j("ca.key"), "2048")
    sh("openssl", "req", "-x509", "-new", "-nodes", "-key", j("ca.key"), "-sha256",
       "-days", "3650", "-subj", "/CN=victoria-bridge-ca", "-out", j("ca.crt"))
    for who, cn, san in (("server", "host.docker.internal", "DNS:host.docker.internal,DNS:localhost,IP:127.0.0.1"),
                         ("client", "victoria", "DNS:victoria")):
        sh("openssl", "genrsa", "-out", j(f"{who}.key"), "2048")
        sh("openssl", "req", "-new", "-key", j(f"{who}.key"), "-subj", f"/CN={cn}", "-out", j(f"{who}.csr"))
        # SAN via a tiny ext file (portable across openssl versions)
        ext = j(f"{who}.ext")
        with open(ext, "w") as fh:
            fh.write(f"subjectAltName={san}\n")
        sh("openssl", "x509", "-req", "-in", j(f"{who}.csr"), "-CA", j("ca.crt"), "-CAkey", j("ca.key"),
           "-CAcreateserial", "-days", "825", "-sha256", "-extfile", ext, "-out", j(f"{who}.crt"))
        os.remove(j(f"{who}.csr")); os.remove(ext)
    for f in ("ca.key", "server.key", "client.key"):
        os.chmod(j(f), 0o600)
    sys.stderr.write(
        f"[bridge] wrote CA + server + client certs to {certs_dir}\n"
        f"  bridge (host):   --certs {certs_dir}\n"
        f"  Victoria (sbx):  CLAUDE_BRIDGE_CLIENT_CERT={j('client.crt')}\n"
        f"                   CLAUDE_BRIDGE_CLIENT_KEY={j('client.key')}\n"
        f"                   CLAUDE_BRIDGE_CA_CERT={j('ca.crt')}\n")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--gen-certs":
        return _gen_certs(args[1] if len(args) > 1 else os.path.expanduser("~/.victoria/bridge-certs"))
    certs = os.path.expanduser("~/.victoria/bridge-certs")
    if args and args[0] == "--certs" and len(args) > 1:
        certs = args[1]
    _serve(certs)


if __name__ == "__main__":
    main()
