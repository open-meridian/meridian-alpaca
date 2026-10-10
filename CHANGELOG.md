# Changelog

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
