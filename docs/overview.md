# Overview

The Alpaca plugin reads Alpaca's US equity and ETF market data and records
it in your deployment's lake, where a plugin that values your book, or shows
prices, reads it under the licence and entitlements you set.

It serves two datasets:

- **Daily** (`alpaca:daily`): each listing's close and daily bar for a
  business date, from Alpaca's consolidated tape (SIP). A date is New York's
  trading day, final at the 16:00 close; until then the day is still forming,
  and is updated in place as it fills. Daily prices are read when a plugin
  asks for them, and each evening the closes of every listing already served
  are recorded unasked.
- **Live** (`alpaca:live`): the latest trade's price and the best bid and
  ask. On a free Alpaca account they come from IEX, one exchange; with real
  time from every exchange (Algo Trader Plus) they are consolidated. Kept
  current once a minute while something is reading them.

Every price is exact, as Alpaca stated it. Every response Alpaca sent is kept
in the plugin's own storage, without your key, so any price can be traced to
what Alpaca said.

It does not serve Alpaca's crypto data (the Coinbase and Kraken plugins do),
trades, quotes, or options, and it does not connect your Alpaca brokerage
account: that is a different role, for a later version.

**A limit today:** the plugin can price only the listings it has resolved by
Alpaca's own symbol. An instrument your deployment knows only from another
source, such as a custodian, is not yet one it can ask Alpaca about; its
Datasets page says so when it happens.
