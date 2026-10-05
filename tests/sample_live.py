"""`make sample SYMBOL=…`: converted rows from Alpaca, with the environment's key.

Never run in CI: it calls Alpaca's real endpoints with the key in
`APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (tests/keys.py), keeps every
response under recordings/ (git-ignored), and prints each converted row as
one JSON line (alpaca/sample.py). Without the key it says which part is
missing and asks nothing.
"""

from __future__ import annotations

import sys

from alpaca import sample

import keys


def main() -> int:
    credentials = keys.from_environment()
    if credentials is None:
        print("sample: no key; set " + " and ".join(keys.missing()), file=sys.stderr)
        return 2
    return sample.main(sys.argv[1:], credentials)


if __name__ == "__main__":
    sys.exit(main())
