"""The developer's key, from the environment: for `make live` and `make sample`
alone, never CI.

Alpaca's own tools name the two parts `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY`; `make live` and `make sample` pass them into the
container by name (`docker run -e APCA_API_KEY_ID`), so no value is ever on a
command line. They are read here, beside the tests, because the plugin
itself reads no environment (`meridian plugin check`'s settings rule): it
takes its key from its secret settings. Nothing here prints, logs or keeps
either value.
"""

from __future__ import annotations

import os

from alpaca.settings import Credentials

KEY_ID = "APCA_API_KEY_ID"
SECRET_KEY = "APCA_API_SECRET_KEY"


def from_environment() -> Credentials | None:
    """The key in the environment, or None where either part is missing."""
    key_id, secret_key = os.environ.get(KEY_ID, ""), os.environ.get(SECRET_KEY, "")
    return Credentials(key_id, secret_key) if key_id and secret_key else None


def missing() -> list[str]:
    """The names of the parts not in the environment (names, never values)."""
    return [name for name in (KEY_ID, SECRET_KEY) if not os.environ.get(name)]
