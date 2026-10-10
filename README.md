# meridian-alpaca

Alpaca's US equity and ETF market data in an [Open Meridian](https://open-meridian.dev)
deployment, as a `dgm` plugin: daily closes and bars, and live prices, put
into the deployment's lake for the plugins that value and show a book.

It is the first half of the retail pair, beside
[meridian-tradier](https://github.com/open-meridian/meridian-tradier): both
pass the same suite of the SDK, unchanged. Alpaca is also a broker; this
repository is named for the vendor so that a `custody` or `ccm` role can
arrive here later without a rename.

**Status:** built on open-meridian 0.22.0 (contract 18), which is not yet on
PyPI. Until it is, this repository's CI is red: every job installs the SDK
it pins, and a developer's machine builds it from the sibling
meridian-python checkout instead. Not released.

## What it serves

| Dataset | What | From | Arrives | Default terms |
|---|---|---|---|---|
| `daily` | each listing's close and daily bar (open, high, low, close, volume, VWAP, trade count) for a business date, New York's day ending at the 16:00 close | Alpaca's consolidated history (SIP), past the free plan's restricted latest 15 minutes | pulled when a reader's read is wanted; pushed once a session's close is final, for the listings it has served | kept; personal use |
| `live` | the latest trade's price (`last`), the best bid and ask | SIP where the key has it in real time (Algo Trader Plus), else IEX, the one exchange a free account sees | pulled for a read; kept current once a minute while a standing want stands | served, not kept; personal use |

- A daily price is keyed by a plain date. A day still forming is recorded
  and restated under the same row as it fills, until its close is final.
- A live price names the exchange its trade printed on, resolved to the
  deployment's venue master by Alpaca's own letter (`V` for IEX); a
  consolidated price names none.
- Every number is exact, read from Alpaca's own text and never through a
  float. A value that does not convert is counted on the Datasets page, never
  recorded.
- Every response is kept first as a raw record in the storage the deployment
  grants the plugin, without the key; every row names the one it came from.
- Not here: Alpaca's crypto (the crypto pair's, meridian-coinbase and
  meridian-kraken), trades and quotes and Alpaca's stream (with the lake's
  next revision), options.

**Instruments another source named.** The lake asks for a price by the
deployment's own instrument ID. An instrument known to the deployment only
by another source's identifiers -- a custodian's symbol and CUSIP, as the
book holds it -- is read from its record, priced under Alpaca's symbol for
the listing, and Alpaca's symbol is added to the record. Only a record
carrying no symbol is declined, not covered. On a runtime from before
contract 18 let a `dgm` read an instrument's record, such an instrument is
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
account's, which does not expire.

1. In Alpaca's dashboard, under **API keys**, generate a key. Alpaca shows
   the secret key once.
2. In the deployment, open the plugin under **Manage**, then **Settings**.
3. Enter the **API key ID** and the **API secret key**. Both are secret
   settings: once saved they are never shown again, logged, put on a page or
   sent anywhere but Alpaca.
4. Open **Connection**: it says whether each part is set (never its value),
   whether Alpaca answered, and which feeds the key is entitled to.
5. On the deployment's **Data sources** page, license `daily` and `live`,
   and entitle the plugins that read prices.

## Checking it live

The live check is the account holder's own, with their own key, on their own
machine; CI never runs it.

1. `make live` asks for the key ID and the secret key at a prompt that does
   not echo, and hands them to a container on its standard input. It prints
   whether Alpaca answered, the feeds the key is entitled to, a week of SPY's
   and BRK.B's closes from SIP, their latest trade and quote, and
   `key_in_raw_records: false`; never the key. What Alpaca answered stays in
   `recordings/`, which git ignores.
2. `make e2e-recorded RECORDED=recordings/<when>` replays that recording
   through the e2e on core's plugin harness.
3. On a development deployment, with the plugin launched and the key entered
   (above): Connection says **Connected**, IEX in real time and SIP past 15
   minutes entitled; after a reader asks for a close, Datasets counts it
   recorded.

## Building it

    make ci-local        lint, the tests (the SDK's dgm suite among them), meridian plugin check, the image, and the e2e
    make e2e             the plugin on core's plugin harness, synthetic, with no key (e2e/run.py)
    make preview         the pages on synthetic data, for a browser beside the kit
    make install-hooks   push runs ci-local first

Everything runs in containers; the host needs Docker and Python 3 for the
e2e's runner. `docs/` is the plugin's documentation, a draft.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Ideas and bug
reports are welcome as issues; see [CONTRIBUTING.md](CONTRIBUTING.md).
