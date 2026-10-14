"""The `environment` setting (from 0.8.1): Sandbox, Alpaca's paper trading
API, by default, so Live is only ever a person's choice -- an upgrade, or a
value never saved, never turns the plugin Live (the product owner,
2026-10-14). The key is synthetic: nothing here reaches Alpaca."""

from __future__ import annotations

import meridian

from alpaca.broker import LIVE_BASE, PAPER_BASE, base_for
from alpaca.settings import (
    DECLARED,
    ENVIRONMENT,
    KEY_ID,
    LIVE,
    SANDBOX,
    SECRET_KEY,
    Config,
    config_from,
)

SETTINGS: tuple[meridian.Setting, ...] = DECLARED.declared()  # type: ignore[attr-defined]
BY_NAME = {setting.name: setting for setting in SETTINGS}
KEY = {KEY_ID: "PKTESTKEYID0000000002", SECRET_KEY: "test-secret-never-kept-0000000000000002"}


def delivered(held: dict[str, str]) -> meridian.Settings:
    """The settings as the SDK hands them over: typed, the declared defaults
    filled in, and the required ones held nothing for named, as the sidecar
    names them."""
    defaults = {s.name: s.default for s in SETTINGS if s.default is not None}
    values: dict[str, str | int | bool | list[dict[str, str]]] = {
        **defaults,
        **{name: BY_NAME[name]._parsed(text) for name, text in held.items()},
    }
    missing = tuple(s.name for s in SETTINGS if s.required and s.name not in held)
    return meridian.Settings(values=values, missing_required=missing)


def test_the_environment_is_first_and_sandbox_by_default() -> None:
    first = SETTINGS[0]
    assert first.name == ENVIRONMENT and first.required and first.label == "Environment"
    assert [(c.value, c.label) for c in first.choices] == [(LIVE, "Live"), (SANDBOX, "Sandbox")]
    assert first.default == SANDBOX
    assert first.description.endswith("Sandbox until a person chooses Live.")
    assert first.roles == ("data", "custody", "connectivity")


def test_an_unset_environment_is_the_sandbox() -> None:
    settings = delivered(KEY)
    assert settings.values[ENVIRONMENT] == SANDBOX and ENVIRONMENT in settings.missing_required
    config = config_from(settings.values)
    assert config.environment == SANDBOX and config.paper
    assert base_for(config.environment) == PAPER_BASE
    # However it arrives: absent, or a value that is not a choice.
    assert config_from(dict(KEY)).environment == SANDBOX
    assert config_from({**KEY, ENVIRONMENT: ""}).environment == SANDBOX
    assert config_from({**KEY, ENVIRONMENT: "LIVE"}).environment == SANDBOX
    assert Config().environment == SANDBOX


def test_live_is_read_only_once_a_person_chooses_it() -> None:
    settings = delivered({**KEY, ENVIRONMENT: LIVE})
    assert settings.missing_required == ()
    config = config_from(settings.values)
    assert config.environment == LIVE and not config.paper
    assert base_for(config.environment) == LIVE_BASE
    assert config.credentials is not None and config.credentials.key_id == KEY[KEY_ID]
