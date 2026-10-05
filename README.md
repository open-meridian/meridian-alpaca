# alpaca

Alpaca's US equity and ETF market data as an Open Meridian `dgm`: a
connectivity spike, made by `meridian plugin new` (meridian-cli 0.1.33's
template) on open-meridian 0.18.0, the retail pair's first half beside
meridian-coinbase and meridian-kraken, the crypto pair. It proves Alpaca's
Market Data API v2 and the conversion into the lake's rows before the lake's
contract exists; it records nothing, because there is nothing to record into
yet. The plan is meridian-design's `plans/the-lake-prices-the-book.md`.

- `src/alpaca/venue.py`: the client. `/v2/stocks/bars` (paged),
  `/v2/stocks/trades`, the latest trade and quote, snapshots; every request
  naming its feed, IEX (free) or SIP (every exchange); at most half Alpaca's
  Basic rate limit (100 calls a minute, one at a time), a 429 waited out.
  Which feeds the key is entitled to is asked once each
  (`Alpaca.entitlements`: IEX, SIP past its latest 15 minutes, SIP in real
  time), and SIP is served for a dataset only where it is. Every response is
  kept first as a raw record (`raw.py`), in the storage the deployment
  grants (`meridian.edge.storage_dir()`), else `recordings/`, which git
  ignores: its request's headers less Alpaca's two authentication headers,
  and any text holding either part of the key redacted.
- `src/alpaca/settings.py`: the key's two parts as **secret settings** (and
  the feed). The plugin reads no environment; the developer's commands take
  the key from `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in
  `tests/keys.py`. Neither part is ever logged, printed, shown or kept.
- `src/alpaca/rows.py`: **the seam**, identical byte for byte to
  meridian-coinbase's and meridian-kraken's: the lake's slice 1 rows as
  plain Python values, to be replaced by the SDK's generated types when the
  lake's contract lands.
- `src/alpaca/assets.py`: a US symbol to the subject, the deployment's
  instrument by the venue's symbol namespaced to the venue (`BRK.B` at
  `alpaca`; decisions/030), priced in USD's ISO 4217 cash instrument. No
  global identifier: Alpaca's market data states none, FIGI included.
- `src/alpaca/convert.py`: Alpaca's values to rows, exact decimals only (its
  prices are JSON numbers, read as Decimals from their text); the daily
  close on the declared exchange day (New York's, ending 16:00), the forming
  day restated; trades with Alpaca's condition codes as reported, no side
  guessed; level-1 quotes in shares. Which feed each row came from is its
  dataset: `daily-iex`, `daily-sip`, `live-iex`, `live-sip`.
- The Connectivity page, under Manage alone: whether Alpaca answers with the
  configured key (each part set or not, never its value), the feeds the key
  is entitled to, the datasets it would serve with their feed and licence
  terms. A `dgm` shows no market data.

Check the gates, and, with a key, look at real rows:

    make ci-local                                     # lint, tests on synthetic exchanges, plugin check, image, seam unchanged
    APCA_API_KEY_ID=… APCA_API_SECRET_KEY=… make live           # the live smoke test; never in CI
    APCA_API_KEY_ID=… APCA_API_SECRET_KEY=… make sample SYMBOL=TQQQ FEED=sip

(Export the two variables in your shell rather than typing them on the
command line, if your shell keeps history.) `make live` and `make sample`
pass the key into the container by name alone, write the real responses
under `recordings/`, and print booleans and rows, never the key.

`tests/test_conformance.py` is the retail pair's suite: the crypto pair's
suite with the equity cases a US listing needs, on the same seam, to be the
same file in meridian-tradier. `make conformance-same` holds rows.py to
meridian-coinbase's, and the suite to meridian-tradier's once it is built.

`AGENTS.md` teaches any coding agent the kit and the live loop; `CLAUDE.md`
and the `develop-live` skill under `.claude/` lead Claude Code to it.
