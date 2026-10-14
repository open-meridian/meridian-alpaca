# Changelog

## 0.8.1 (unreleased)

- **Live is a person's choice.** `environment` defaults to `sandbox`,
  Alpaca's paper trading API, not `live`: an upgrade, or an Environment
  never saved, no longer turns the plugin Live. Over a deployment that
  never saved one, 0.8.0 came up on Live, where Alpaca's live Trading API
  refuses a paper account's key (40110000). Its description says Live is
  chosen by a person (the product owner, 2026-10-14).
- Nothing else changes. Tests: an unset environment is the sandbox, and
  Live only once chosen.

## 0.8.0 (unreleased)

- On open-meridian 0.27.0 (contract 23): the pins moved as the SDK's
  0.26.0 to 0.27.0 step moves them; meridian 0.1.43's checks.
- The `custody` role, beside `data` and `connectivity`, in SnapTrade
  0.16.0's shape: the account Alpaca's key reaches (`/v2/account`), its
  positions (`/v2/positions`) and its activities (`/v2/account/activities`),
  read every **Read every**, reported in the contract's words: the account,
  margin by its margin classification; its sync state; a statement as of
  the day in New York, a row per position and the cash, the settled
  quantity and pending lines derived from the account's unsettled fills and
  the deployment's settlement dates; every activity, converted to its kind
  or kept as reported, a fill's amount derived. Alpaca states the currency
  of every amount by its account's (USD), read as the product owner ruled
  for OANDA's account; each resolve offers it with its provenance reported
  from the account's raw record (`ResolveIdentifierRequest.provenance`).
- Custody's pages: **Account links** (`read_account_links`, `link_account`,
  checked by `check_link_account`), **Statements** (`read_statements`) and
  **History** (`read_account_activities`); **Raw records** serves custody
  too. Settings: custody's **Read every** and **Stale after**. A third kind
  of raw record, `activity` (2555 days), SnapTrade's.
- Connectivity, contract 23: an order placed in Alpaca's own app has its
  fills reported on no placement, held unmatched until the desk's `ems`
  accepts or answers them DK; the accept's placement takes the order's
  later fills. Only fills after the plugin first watched the account are
  reported (`trading/originated.json`). The catalogue's
  `execution_acknowledgement` is none: Alpaca takes none, so a DK tells it
  nothing. Every fill names Alpaca's order ID.
- The suites: custody passes every case but 12 Alpaca never presents, each
  with why; connectivity passes every case but 10 not presented, among them
  `reports-a-charge` (`about: charges without a fill`) and v23's charge
  re-reported on an accepted placement and value-date roll; data marks
  v23's three outcome cases not presented. No suite test is skipped or
  expected to fail.
- `make e2e`: Alpaca's account linked to the e2e's account, its opening
  balance recorded from Alpaca's statement by the desk (now holding
  `operations` too); an order placed in Alpaca's app reported on no
  placement and accepted to a new order; and after every fill, the book
  reconciled against Alpaca's statement with no break.

## 0.7.0 (unreleased)

- The `connectivity` role (contract 22), beside `data`, in SnapTrade
  0.16.0's shape: the plugin places the placements naming its instance at
  Alpaca's Trading API, in the account its `ems` mapped
  (`AccountScope.venue_account_map`), with the placement's ID as the client
  order ID, and reports acknowledgements, rejections (`unsupported` with
  nothing sent, or `venue rejected` with Alpaca's code and words),
  replaces, restatements, cancels, expiries, done for day, fills and busts,
  each with its raw record. A status the mapping does not know is outcome
  unknown and resolved by reading; a send never answered is asked about,
  never sent again, across a restart (`trading/sent.json`).
- Fills: as Alpaca states them on its trade updates stream, and inferred
  from the orders read every 15 seconds besides, each derived to the same ID
  (the order and the filled quantity it brought it to); every fill's
  settlement date is the deployment's (ComputeDate, with its basis), since
  Alpaca states none.
- Cancel and replace read from `Placement.change_requests`; a replace is
  Alpaca's `PATCH`, its new order followed. Pauses and stops honoured, a
  stop's cancels reported naming its entry.
- The venue catalogue per venue account code: none on a read-only account;
  once trading is enabled on it by the connectivity role's admin
  (**Trading**, `enable_trading`, at `/admin/connections/enable-trading`),
  Alpaca's order types and times in force.
- Settings: `environment` (`live`, or `sandbox`: Alpaca's paper trading
  API), first; the key and sample data now serve both roles. The key's
  value carries over.
- Two kinds of raw record, each with its window an admin sets: `responses`
  (30 days) and `trading` (2555 days), archivable; **Raw records** follows
  a fact's reference.
- The connectivity suite: 19 cases pass; 7 are not presented, each with
  why (calendars, locates, a fill's place of execution, compliance waits);
  `reports-a-charge` is not produced, Alpaca stating no fee on a placement
  that filled nothing (for the product owner to rule on).
- `make e2e` runs the order path after the data steps (e2e/orders.py),
  with a stand-in desk (e2e/desk.py), against a simulated brokerage
  (src/alpaca/synthetic_broker.py). Nothing reaches Alpaca's trading API.

## 0.6.0 (unreleased)

- On open-meridian 0.26.0 (contract 22): the pins moved as the SDK's
  0.25.0 to 0.26.0 step (`meridian plugin migrate --to 0.26.0`) moves them,
  rewriting nothing. Contract 22 is the order path and settlement
  calendars; this plugin places no order and works out no date, so nothing
  else changes.
- The data suite's two calendar cases (`reads-a-calendar`,
  `asks-for-a-settlement-date`) are not presented, each with why: Alpaca's
  bars are dated as Alpaca states them, and nothing here needs a
  settlement calendar or a settlement date.
- AGENTS.md says to name a declared algorithm on autonomous work, and to
  work out a date in core (`compute_date`, `read_calendar`), never by hand.

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
