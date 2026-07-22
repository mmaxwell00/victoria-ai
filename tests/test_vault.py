"""Tests for the encrypted credentials vault + its API (names in, never out)."""
import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient, ASGITransport

from victoria.vault.store import SecretsVault
from victoria.main import app


@pytest.fixture
def vault(tmp_path):
    # Explicit key + temp path → never touches the real Keychain/vault.
    return SecretsVault(path=str(tmp_path / "vault.enc"), key=Fernet.generate_key())


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_set_list_names_and_delete(vault):
    vault.set("SLACK_TOKEN", "xoxb-secret")
    vault.set("GITHUB_TOKEN", "ghp_secret")
    assert vault.names() == ["GITHUB_TOKEN", "SLACK_TOKEN"]   # sorted names, no values
    assert vault.exists("SLACK_TOKEN")
    assert vault.delete("GITHUB_TOKEN") is True
    assert vault.names() == ["SLACK_TOKEN"]
    assert vault.delete("GITHUB_TOKEN") is False


def test_set_rejects_empty(vault):
    with pytest.raises(ValueError):
        vault.set("", "x")
    with pytest.raises(ValueError):
        vault.set("NAME", "")


def test_encrypted_at_rest(vault):
    vault.set("API_KEY", "super-secret-plaintext")
    raw = vault.path.read_bytes()
    assert b"super-secret-plaintext" not in raw      # not stored in the clear
    assert b"API_KEY" not in raw


def test_wrong_key_cannot_read(tmp_path):
    p = str(tmp_path / "vault.enc")
    SecretsVault(path=p, key=Fernet.generate_key()).set("K", "v")
    # A different key can't decrypt → empty, not an exception.
    assert SecretsVault(path=p, key=Fernet.generate_key()).names() == []


def test_persists_across_instances(tmp_path):
    key = Fernet.generate_key()
    p = str(tmp_path / "vault.enc")
    SecretsVault(path=p, key=key).set("K", "v")
    assert SecretsVault(path=p, key=key).names() == ["K"]


# ---------------------------------------------------------------------------
# Resolution (transport edge only)
# ---------------------------------------------------------------------------

def test_resolve_substitutes_refs(vault):
    vault.set("SLACK_TOKEN", "xoxb-123")
    out = vault.resolve({"env": {"AUTH": "Bearer ${vault:SLACK_TOKEN}"}, "keep": "plain"})
    assert out["env"]["AUTH"] == "Bearer xoxb-123"
    assert out["keep"] == "plain"


def test_resolve_unknown_ref_left_intact(vault, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)   # ensure no env fallback exists
    assert vault.resolve("${vault:NOPE}") == "${vault:NOPE}"


def test_resolve_falls_back_to_env(vault, monkeypatch):
    # A ref not in the encrypted store resolves from the environment — this is
    # how a Docker Sandbox's proxy-injected credentials resolve with no config
    # change.
    monkeypatch.setenv("GITHUB_TOKEN", "gho_from_env")
    assert not vault.exists("GITHUB_TOKEN")
    assert vault.resolve("Bearer ${vault:GITHUB_TOKEN}") == "Bearer gho_from_env"


def test_resolve_vault_takes_precedence_over_env(vault, monkeypatch):
    # A real stored secret must win over an env var of the same name.
    vault.set("TOK", "from-vault")
    monkeypatch.setenv("TOK", "from-env")
    assert vault.resolve("${vault:TOK}") == "from-vault"


def test_resolve_handles_lists_and_nesting(vault):
    vault.set("T", "tok")
    out = vault.resolve({"headers": [{"X": "${vault:T}"}]})
    assert out["headers"][0]["X"] == "tok"


# ---------------------------------------------------------------------------
# API — names in, never out
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the API's singleton vault at a throwaway store.
    import victoria.vault.store as store_mod
    test_vault = SecretsVault(path=str(tmp_path / "vault.enc"), key=Fernet.generate_key())
    monkeypatch.setattr(store_mod, "vault", test_vault)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_api_store_then_list_names_only(client):
    async with client as c:
        r = await c.post("/v1/vault", json={"name": "OPENAI_KEY", "value": "sk-should-stay-secret"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["name"] == "OPENAI_KEY"
        # Response must NOT contain the value anywhere.
        assert "sk-should-stay-secret" not in r.text

        r2 = await c.get("/v1/vault")
        assert r2.json()["names"] == ["OPENAI_KEY"]
        assert "sk-should-stay-secret" not in r2.text     # list never exposes values


@pytest.mark.asyncio
async def test_api_rejects_empty(client):
    async with client as c:
        r = await c.post("/v1/vault", json={"name": "", "value": "x"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_delete(client):
    async with client as c:
        await c.post("/v1/vault", json={"name": "K", "value": "v"})
        r = await c.delete("/v1/vault/K")
        assert r.json()["ok"] is True
        assert (await c.get("/v1/vault")).json()["names"] == []
