# Overview

The Alpaca plugin reads Alpaca's US equity and ETF market data and records
it in your deployment's lake, where a plugin that values your book, or shows
prices, reads it under the licence and entitlements you set.

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

Every price is exact, as Alpaca stated it. Every response Alpaca sent is kept
in the plugin's own storage, without your key, so any price can be traced to
what Alpaca said.

It does not serve Alpaca's crypto data (the Coinbase and Kraken plugins do)
or options, and it does not connect your Alpaca brokerage account: that is a
different role, for a later version. Alpaca does not say which side of a
trade took liquidity, so the plugin does not either.

An instrument your deployment knows only from another source, such as a
custodian, is priced under Alpaca's symbol for it; a free account streams at
most 30 listings at once.
