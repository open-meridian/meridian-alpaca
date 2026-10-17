# Overview

The Alpaca plugin reads Alpaca's US equity and ETF market data and records
it in your deployment's lake, where a plugin that values your book, or shows
prices, reads it under the licence and entitlements you set.

It holds three roles, `data`, `custody` and `connectivity`: the first-use path, the common pages and the Summary figures of each are described once, on [Using a data plugin](https://open-meridian.dev/how-to/using-a-data-plugin/) and [Using a custody plugin](https://open-meridian.dev/how-to/using-a-custody-plugin/). These sections cover what is Alpaca's own.

## Its datasets

It serves three datasets:

- **Daily** (`alpaca:daily`): each listing's close and daily bar for a
  business date, from Alpaca's consolidated tape (SIP). A date is New York's
  trading day, final at the 16:00 close; until then the day is still forming,
  and is updated in place as it fills. Daily prices are read when a plugin
  asks for them, and each evening the closes of every listing already served
  are recorded unasked.
- **Live** (`alpaca:live`): the latest trade's price and the best bid and
  ask. On a free Alpaca account they come from IEX, one exchange; with real
  time from every exchange (Algo Trader Plus) they are consolidated. Kept
  current once a minute while something is reading them; the best bid and
  offer, with its sizes, streamed as it changes, at most once a second.
- **Trades** (`alpaca:trades`): each trade, streamed from the same feed
  while something is reading the listing, with what it may count for in a
  day's high, low, open, close and volume, converted from Alpaca's
  condition codes. Kept two days by default, so a plugin that missed some
  catches up. If the stream drops, the plugin reconnects and fills the gap
  from Alpaca's trade history.

## How it records

Every price is exact, as Alpaca stated it. Every response Alpaca sent is kept
in the plugin's own storage, without your key, so any price can be traced to
what Alpaca said.

It does not serve Alpaca's crypto or options data, and it trades no
options. Alpaca does not say which side of a
trade took liquidity, so the plugin does not either.

An instrument your deployment knows only from another source, such as a
custodian, is priced under Alpaca's symbol for it; a free account streams at
most 30 listings at once.

## The account at Alpaca

As a custody plugin it reads the account at Alpaca every **Read every** as the
custodian's view: its sync state, a statement as of the day in New York (a
row per position, and the cash), and its activity -- fills, dividends,
interest, fees, taxes, transfers, deposits, withdrawals and journals, any
other type kept as reported. Alpaca's account states no type; its margin classification is a margin account's. Alpaca states the account's currency, and no settled quantity: each fill not yet settled is pending on the deployment's settlement date for it. Nothing is reported for an account
nothing links.

## Orders at Alpaca

As a connectivity plugin it places the orders your execution plugin (an
`ems`) sends it at Alpaca, in the account its code names, and reports what
Alpaca did with each: acknowledged, rejected, replaced, cancelled, filled.
Alpaca's trade updates stream reports each fill, and the plugin reads the orders every 15 seconds besides, so a fill missed while the stream was down is found. Every fill carries the deployment's settlement date for it, and
waits for it rather than go with a guess. An order placed in Alpaca's own app is
never recorded as an order: its fills are reported on no order, for your
execution plugin to accept or answer DK. A new account is read-only until
the connectivity role's admin enables trading on it.
