# Changelog

## 0.5.0 (unreleased)

- On open-meridian 0.25.0 (contract 21): the pins moved as the SDK's
  0.24.0 to 0.25.0 step (`meridian plugin migrate --to 0.25.0`) moves them,
  rewriting nothing. Contract 21 lets a plugin's
  autonomous work act as its role's service account; this plugin acts on
  nothing of the deployment's by itself, so nothing else changes.
- AGENTS.md says how a plugin acts on its own: only as its role's service
  account, through `meridian.runs_as(role)`.

## 0.4.0 (unreleased)

- On open-meridian 0.24.0 (contract 20): the role is `data` (a data
  plugin), `dgm` until now.
- Connection and Datasets are the data role's pages
  (spec/plugin-pages-follow-their-role): Connection's "Key", "Limits and
  use" and "Stream", Datasets' "Served", "Wants" and "Subjects", in the
  role's order and words, with Alpaca's feeds, stream session, feed column
  and "Kept current" at the role's extension points; the "not set up yet"
  states ("No key yet", linking to Settings; "No wants yet"); and
  `read_connection` and `read_datasets` answering the role's common fields
  with Alpaca's beside. The data role's figures on the Summary.
- Datasets says whether a deployment admin has licensed each dataset,
  read from the deployment on start and every minute.
- A restart's gap backfilled: the first time a listing's trades are wanted
  after a restart, the trades since the last one recorded, from Alpaca's
  history, two days back at most.
- The setting `synthetic` is `sample_data` (Sample data), the SDK's.
- Fixed: whether the key has SIP in real time is asked by SIP's trades of
  the last minute, not by a latest quote, and a SIP refusal falls back to
  IEX: the latest quotes come from the feed the key is entitled to (IEX on
  a free or a paper key). The refused probe is no longer the Connection
  page's last error.
- Fixed: a symbol Alpaca has nothing for (TCH4) is declined once for the
  date, not asked and declined again every minute.
- The data suite's v20 cases: a trade recorded, a restart's gap backfilled
  and its datasets' licences read, presented; the taker's side and the
  maker's side inverted not presented (Alpaca states no aggressor).

## 0.3.0 (unreleased)

- On open-meridian 0.23.0 (contract 19): the lake's trades and quotes.
- `trades`, a new dataset: each trade with the platform's trade attributes,
  converted from its conditions on its tape (CTA's and UTP's letters), a
  code with no conversion kept as reported; no aggressor, which Alpaca never
  states; a cancel, an error or a correction the trade's next version.
  Streamed for the listings under a standing want, pulled for a range;
  kept two days by default.
- `live` gains quotes: the best bid and offer with its sizes, one side
  empty where Alpaca sends none, streamed at most once a second a listing.
- Alpaca's stream over WebSocket (the standard library's own client), with
  reconnect, subscriptions made again from the standing wants, and the gap
  caught up from Alpaca's trade history; a free account's 30
  listings kept to, the rest declined, not entitled.
- Fixed: a second record of one listing is recorded under its own row key
  (the key names the record, `SPY:1d:2026-10-08@LCL-...`), not under the
  first's, which the lake versioned as a restatement; a day read again
  unchanged keeps its raw record, so the lake keeps it unchanged. Rows
  recorded by 0.2.0 keep their keys.
- The SDK's suite with its 1b cases: four not presented, each with why.
- Connection shows the stream; Datasets searches and pages the standing
  wants (kit 0.11.0).

## 0.2.0 (unreleased)

- A `dgm` on open-meridian 0.22.0 (contract 18): the catalogue's `daily`
  (closes and daily bars from SIP, New York's day to the 16:00 close, pulled
  and pushed) and `live` (last, bid and ask, SIP in real time where the key
  has it, else IEX; pulled and kept current while a standing want stands),
  each with Alpaca's personal-use terms as its default licence.
- Wants answered or declined per subject; a forming day restated under one
  row key until final; listings resolved by Alpaca's symbol, venues by
  Alpaca's exchange letter, misses reported.
- An instrument the book holds from another source, known only by that
  source's identifiers (a custodian's symbol and CUSIP), read from its
  record and priced under Alpaca's symbol.
- Connection and Datasets under Manage, each a read tool
  (`read_connection`, `read_datasets`).
- The SDK's `dgm` suite, unchanged; an e2e on core's plugin harness with the
  synthetic exchange, and `make e2e-recorded` with a developer's recording;
  `make live`, the account holder's live check.

## 0.1.0

- The spike: connectivity and conversion against Alpaca's Market Data API
  v2, before the lake's contract (open-meridian 0.18.0). Its live check
  passed on 2026-10-05.
