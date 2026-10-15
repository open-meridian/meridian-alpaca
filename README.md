# meridian-alpaca

Alpaca's US equity and ETF market data in an [Open Meridian](https://open-meridian.dev)
deployment, the account as Alpaca states it, and the orders a desk places
at Alpaca. Three roles:

- `data`: daily closes and bars, live prices and quotes, and trades, put
  into the deployment's lake for the plugins that value and show a book.
- `connectivity` (from 0.7.0, contract 22): the placements a desk's `ems`
  names this instance on, placed at Alpaca's Trading API, paper or live,
  and what Alpaca did with them: acknowledgements, rejections, replaces,
  cancels, fills with their settlement dates, and busts; from 0.8.0
  (contract 23) the fills of orders placed in Alpaca's own app too,
  reported on no placement for the desk to accept or answer DK.
- `custody` (from 0.8.0, contract 23): Alpaca's view of the account -- its
  positions and cash, settled and pending, and its activity -- which the
  deployment's operations reconcile the book against.

It is the first half of the retail pair, beside
[meridian-tradier](https://github.com/open-meridian/plugin-tradier): both
pass the same suites of the SDK, unchanged.

**Status:** 0.9.0, on open-meridian 0.27.0 (contract 23), proven locally
against core v23's runtime and plugin harness, built locally. Not released.

## What it serves

| Dataset | What | From | Arrives | Default terms |
|---|---|---|---|---|
| `daily` | each listing's close and daily bar (open, high, low, close, volume, VWAP, trade count) for a business date, New York's day ending at the 16:00 close | Alpaca's consolidated history (SIP), past the free plan's restricted latest 15 minutes | pulled when a reader's read is wanted; pushed once a session's close is final, for the listings it has served | kept; personal use |
| `live` | the latest trade's price (`last`), the best bid and ask; the best bid and offer as a quote, with its sizes | SIP where the key has it in real time (Algo Trader Plus), else IEX, the one exchange a free account sees | pulled for a read; while a standing want stands, prices kept current once a minute and quotes streamed, at most one a second per listing | served, not kept; personal use |
| `trades` | each trade with the platform's trade attributes, converted from its conditions | the same feed as `live` | streamed for the listings under a standing want; pulled for a range a reader wants | kept two days; personal use |

- A daily price is keyed by a plain date. A day still forming is recorded
  and restated under the same row as it fills, until its close is final.
- A live price names the exchange its trade printed on, resolved to the
  deployment's venue master by Alpaca's own letter (`V` for IEX); a
  consolidated price names none.
- Every number is exact, read from Alpaca's own text and never through a
  float. A value that does not convert is counted on the Datasets page, never
  recorded.
- Every response, and every stream message, is kept first as a raw record in
  the storage the deployment grants the plugin, without the key; every row
  names the one it came from.
- Each row under its own record's key: where the deployment holds two
  records of one listing (one Alpaca's, one a custodian's, not yet merged),
  each record's rows are its own, the key naming the record
  (`SPY:1d:2026-10-08@LCL-...`), never one recorded over the other. A day
  read again unchanged is kept unchanged. Rows recorded before 0.3.0 keep
  their keys and stand beside the new ones.
- Not here: Alpaca's crypto (the crypto pair's, meridian-coinbase and
  meridian-kraken), options.

## Trades and quotes, streamed

- **What is streamed** is what the lake keeps current: the listings under a
  standing want, of the live prices, the quotes or the trades, and nothing
  while there is none. A free account streams IEX alone, at most 30
  listings; a listing past that is declined, not entitled, and counted on
  the Datasets page.
- **A trade's attributes** are converted from its conditions, on its tape
  (the CTA's letters on tapes A and B, the UTP's on C): whether it may set
  the high and low, the open, the close and the volume, of the consolidated
  bar and of its exchange's, and what it was (an odd lot, extended hours,
  an intermarket sweep, an opening or closing auction print, and so on). A
  code with no conversion is kept as reported, its eligibilities not known,
  and counted. No aggressor: Alpaca's stock trades never say which side
  took liquidity, and none is guessed.
- **A withdrawal** (a cancel or an error) or a **correction** is the trade's
  next version under its row key.
- **Reconnect and catch-up.** A stream that breaks is opened again, a second
  later and then doubling to a minute, and subscribed again from what is
  wanted. The gap is read from Alpaca's trade history, and only the trades
  not yet recorded are sent, so the lake holds every trade and a reader
  that lost some catches up from it.
- **A restart's gap, backfilled** (contract v20). The lake delivers each
  standing want again to a plugin started anew; the first time a listing's
  trades are wanted after a restart, the plugin reads the last trade it
  recorded of it from the lake and records what Alpaca's trade history
  states since, two days back at most (the trades' default retention),
  before it streams the listing again.
- **The suite.** Six of its cases are not presented, each with why: a
  currency's rate and a crypto asset's price (kinds Alpaca's stock data never
  publishes), the two about the side that took liquidity (a field,
  `Trade.aggressor`, Alpaca never states), and contract v22's two calendar
  cases: it dates each bar by the session's close as Alpaca states it, and
  never reads a settlement calendar or asks for a settlement date.

**Live data from the feed the key is entitled to.** Whether the key has SIP
in real time is asked by SIP's trades of the last minute, which a free or a
paper account's key is refused, never by a latest quote; until it has, the
latest quotes, the latest trades and the stream are IEX's. Should SIP refuse
a live call, the plugin takes the key not to have it and asks IEX instead.

**A symbol Alpaca has nothing for** (a plan fund a custodian names, such as
TCH4) is declined once for the date it was wanted for, not asked of Alpaca
and declined again each minute a standing want is kept current; the next
day, once more. A decline because Alpaca failed is asked again.

**Instruments another source named.** The lake asks for a price by the
deployment's own instrument ID. An instrument known to the deployment only
by another source's identifiers -- a custodian's symbol and CUSIP, as the
book holds it -- is read from its record, priced under Alpaca's symbol for
the listing, and Alpaca's symbol is added to the record. Only a record
carrying no symbol is declined, not covered. On a runtime from before
contract 18 let a data plugin read an instrument's record, such an instrument is
declined, and the Datasets page says why.

## Alpaca's terms, summarised

Not legal advice: read Alpaca's own terms before relying on this.

- Alpaca's market data is for **personal and non-commercial use**: a
  non-professional subscriber receives it "solely for their personal,
  non-business use".
- **Nothing is republished or redistributed** without Alpaca's written
  consent. A personal deployment may keep its own copy, for the account
  holder's own use.
- The free plan gives real-time data from **IEX alone**, 30 symbols on the
  stream and 200 calls a minute, with the latest 15 minutes of history
  restricted; Algo Trader Plus ($99 a month when read) gives every US exchange
  in real time.

So each dataset's default licence is one person's (`personal_use`), shown and
used by its holder. The deployment admin confirms or changes it on the Data
sources page, which warns when more than one person can read a plugin
entitled to a one-person dataset. Neither this plugin nor its documentation
says a deployment meets Alpaca's terms: the licence records what the admin
entered.

## Entering the key

The plugin needs an Alpaca account's API key: a free account's, or a paper
account's, which does not expire. Market data reads the same with either;
the key decides which account connectivity trades.

1. In Alpaca's dashboard, under **API keys**, generate a key: the paper
   account's to trade with simulated money, the live account's to trade
   for real. Alpaca shows the secret key once.
2. In the deployment, open the plugin under **Manage**, then **Settings**.
3. Choose **Environment**: **Sandbox** is Alpaca's paper trading API
   (paper-api.alpaca.markets), simulated money; **Live** is its live
   Trading API (api.alpaca.markets). It must match the key. It is Sandbox
   until a person chooses Live (from 0.9.0): neither an upgrade nor an
   Environment never saved turns the plugin Live.
4. Enter the **API key ID** and the **API secret key**. Both are secret
   settings: once saved they are never shown again, logged, put on a page or
   sent anywhere but Alpaca.
5. Open **Connection**: under **Key** it says whether each part is set
   (never its value), whether Alpaca answered, and which feeds the key is
   entitled to. With no key it says "No key yet" and links to Settings.
6. On the deployment's **Data sources** page, license `daily`, `live` and
   `trades`, and entitle the plugins that read prices, trades and quotes.

## Trading at Alpaca

With the `connectivity` role (granted by a deployment admin, as every role
is), the plugin is a desk's connection to Alpaca:

1. A deployment admin makes the plugin available to the desk's `ems`, and
   the `ems`'s admin links it and maps a book account to the account's
   venue account code: Alpaca's account number, shown on **Trading**.
2. **Trading** (under Manage, the connectivity role's admin) says plainly
   which environment the account is in -- **Paper**, simulated money, or
   **Live** -- and each account the key reaches. A new account is
   **read-only**: every placement on it is rejected `unsupported`, nothing
   sent, until the connectivity role's admin presses **Enable trading**
   (`enable_trading`, checked by `check_enable_trading`). Its catalogue is
   then published: `market` and `limit`, each `day`, `gtc`, `ioc` or `fok`;
   `stop` and `stop_limit`, each `day` or `gtc`. Alpaca takes no
   good-till-date order.
3. Each placement naming the instance is placed at Alpaca with the
   placement's ID as its client order ID. Alpaca's trade updates stream
   reports each fill as Alpaca states it, and the plugin reads the orders
   every 15 seconds besides, so a fill missed while the stream was down is
   found, derived to the same ID. Every fill carries the deployment's
   settlement date for it (Alpaca states none), and waits for it rather than
   go with a guess.
4. A replace the desk asks for is Alpaca's `PATCH /v2/orders/{id}`: Alpaca
   puts a new order in the old one's place, which the plugin follows, the
   old one's fills counted on. A cancel is Alpaca's `DELETE`.
5. A pause on the plugin, the account or All accounts holds new placements
   back; a stop also cancels at Alpaca what it is working, each cancel
   reported naming the stop. A placement sent but not answered is asked
   about on the next read, never sent again, across a restart too
   (`trading/sent.json` in the plugin's storage).

An order the account holder places in Alpaca's own app (contract 23) is
never recorded as an order: its fills are reported on no placement, the
venue account code, instrument, side and Alpaca's order and client order IDs
named, and wait in the book unmatched until the desk's `ems` accepts them or
answers them DK. Alpaca takes no execution acknowledgement, so it is told
nothing either way (the catalogue's `execution_acknowledgement` is none).
Once accepted, the order's later fills go on the placement the accept
created. Only fills after the plugin first watched the account are
reported: an order filled before then is the account's history, which its
opening balance holds. Every exchange with the Trading API is kept as a raw record
of the `trading` kind (seven years by default, an admin's setting, archived,
kept or deleted past it), and each read of the orders under `responses` (30
days); **Raw records** (Open and View) follows a fact's reference to the
record, for a person who may read the account.

What Alpaca never states, the plugin does not invent: a fill's place of
execution, a locate, and a fee charged on any order (Alpaca's fees are
daily account activity naming no order). The connectivity suite marks
those cases not presented, with why, as it marks a value-date roll; every
other case passes, a bust among them (Alpaca busts a fill with its
`trade_bust` event, the order's filled quantity falling).

## The account at Alpaca

With the `custody` role (from 0.8.0), the plugin reads the account every
**Read every** (300 seconds by default; **Stale after**, 36 hours, says how
long it may go unanswered before the account is reported stale), from
`/v2/account`, `/v2/positions` and `/v2/account/activities`, each answer a
raw record first:

1. A deployment admin grants the plugin the accounts it may link to; its
   admin links Alpaca's account to one of them on **Account links** (under
   Manage), or, a deployment admin, to a new account created in one step.
   Nothing is reported for an account nothing links.
2. Each read reports the account's sync state (current, needs sign-in when
   Alpaca refuses the key, disabled when Alpaca says it is closed or
   blocked, stale), and a statement as of the day in New York: a row per
   position, and the cash. Alpaca's account states no type; its margin
   classification (multiplier 1, 2 or 4) is a margin account's.
3. Alpaca states the currency of the account's amounts by the account's own
   `currency` (USD), read, not derived. It states no settled quantity: each
   fill not yet settled is pending on the deployment's settlement date for
   it, and the settled quantity is the quantity less them, said by its
   provenance.
4. Its activity: fills (their amount the quantity times the price, derived),
   dividends, interest, fees, taxes, splits, corporate actions, transfers,
   deposits, withdrawals and journals, any other type kept as reported.
   **Statements** and **History** (Open and View) show them to a person who
   may read the linked account; a raw record kept under `activity` (seven
   years by default).

The custody suite passes but for the cases Alpaca never presents, each
marked not presented with why: a connection late by design or holdings not
served, a cash or retirement account, a money market fund counted in cash,
a currency not stated, a reinvestment, a plan's own fund code, and a
calendar's days.

**The live paper run is the product owner's, by hand.** The tests and the
e2e trade with a simulated brokerage (src/alpaca/synthetic_broker.py) and
never reach Alpaca's paper or live trading API.

## Checking it live

The live check is the account holder's own, with their own key, on their own
machine; CI never runs it.

1. `make live` asks for the key ID and the secret key at a prompt that does
   not echo, and hands them to a container on its standard input. It prints
   whether Alpaca answered, the feeds the key is entitled to, a week of SPY's
   and BRK.B's closes from SIP, their latest trade and quote, twenty seconds
   of Alpaca's stream for them (the condition codes seen, and any not
   converted), and `key_in_raw_records: false`; never the key. What Alpaca answered stays in
   `recordings/`, which git ignores.
2. `make e2e-recorded RECORDED=recordings/<when>` replays that recording
   through the e2e on core's plugin harness.
3. On a development deployment, with the plugin launched and the key entered
   (above): Connection's Mode says **Live**, the Summary's Connection figure
   **Answering**, IEX in real time and SIP past 15 minutes entitled, SIP in
   real time not; after a reader asks for a close, Datasets counts it
   recorded; after a reader asks for a listing's latest quote, Connection's
   **Stream** says it streams that listing, and Datasets counts its trades
   and quotes.

## Building it

    make ci-local        lint, the tests (the SDK's data, custody and connectivity suites among them), meridian plugin check, the image, and the e2e
    make e2e             the plugin on core's plugin harness, on sample data, with no key (e2e/run.py), the order path, custody and a reconciliation among it (e2e/orders.py)
    make preview         the pages on sample data, for a browser beside the kit
    make install-hooks   push runs ci-local first

Everything runs in containers; the host needs Docker and Python 3 for the
e2e's runner. `docs/` is the plugin's documentation, a draft.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Ideas and bug
reports are welcome as issues; see [CONTRIBUTING.md](CONTRIBUTING.md).
