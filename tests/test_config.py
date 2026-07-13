"""Config robustness: an unknown/typo'd/ahead-of-code env var must not crash
the app (it used to raise at import), and such vars are surfaced, not silent."""
from victoria.config import Settings, _warn_unknown_env_keys, settings


def test_settings_ignores_unknown_field():
    """extra='ignore' → an unrecognized input is dropped, not a hard error."""
    s = Settings(_env_file=None, totally_made_up_setting="x")  # must not raise
    assert not hasattr(s, "totally_made_up_setting")
    # a real field still loads
    assert s.model_runner_model


def test_warn_unknown_env_keys_flags_only_typos(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment line\n"
        "MODEL_RUNNER_MODEL=ai/qwen2.5\n"        # known → not flagged
        "export TELEGRAM_BOT_TOKEN=abc\n"        # known (export prefix) → not flagged
        "WOBBLE_FROB=oops\n"                     # unknown → flagged
    )
    assert _warn_unknown_env_keys(settings, str(env)) == ["WOBBLE_FROB"]


def test_warn_unknown_env_keys_missing_file(tmp_path):
    assert _warn_unknown_env_keys(settings, str(tmp_path / "nope.env")) == []


def test_app_and_settings_import_cleanly():
    """Boot smoke test: loading Settings and importing the app must not raise —
    the exact failure mode that took Victoria down when a stray env var was set."""
    Settings()  # reads the real .env from CWD; extra='ignore' keeps it safe
    from victoria.main import app
    assert app is not None
