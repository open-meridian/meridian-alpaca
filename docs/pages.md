# Its pages

Connection and Datasets are the data role's, the same on every data plugin,
under Manage for an admin of the plugin's data role. Neither shows a price: prices are read by the
plugins entitled to them. Each page is also a tool an agent can call through
your deployment's MCP connection, answering exactly what the page shows,
under the same names on every data plugin.

## Connection (`read_connection`)

- **Key:** the mode (Live, Sample data or No key); whether the API key ID
  and the API secret key are each set, never their values; what Alpaca
  serves your key (IEX in real time, every US exchange past 15 minutes,
  every US exchange in real time), and what it does not. With no key, "No
  key yet", linking to Settings.
- **Limits and use:** the plugin spends at most 100 calls a minute, half of
  Alpaca's 200 on the free plan; calls since it started, any answered "too
  many requests" and waited out; the last call and the last error.
- **Stream:** how many sessions stream; then Alpaca's own rows: its state
  and feed, how many listings of how many your key may stream and since
  when, reconnects and trades caught up, messages, the last message and the
  stream's last error.

## Datasets (`read_datasets`)

- **Served:** each dataset as its catalogue declares it: what it serves, how
  its rows arrive, its day, its default terms and whether a deployment admin
  has licensed it, whether Alpaca serves your key it, and what it recorded
  and declined since the plugin started, and when it last recorded; the
  feed each reads (Alpaca's column).
- **Wants:** the standing wants it keeps current, the latest first: of
  which dataset, asking what, for how many subjects, what each was answered
  with, and whether its listings are streamed. With none, "No wants yet".
- **Subjects:** whether the deployment lets the plugin read an instrument's
  record.
- **Kept current** (Alpaca's): how many listings it knows and streams, and
  how many subjects were declined today, each once for the date.

## Account links

Under Manage, for an admin of the custody role (`read_account_links`). The
Alpaca account the API key reaches, by its number, beside the deployment's
accounts granted to this plugin, and the links between them. Link, create a
new account (a deployment admin), or unlink.

## Statements and History

Under Open and View, for people who may read the linked account
(`read_statements`, `read_account_activities`). **Statements**: its
custodian, the day its last statement is as of, and its state at Alpaca.
**History**: its activity as Alpaca reported it, the latest first.

## Trading

Under Manage, for an admin of the connectivity role (`read_trading`). The
environment, Paper or Live, said plainly; each account the API key
reaches by its venue account code, read-only or with trading enabled, the
order types its catalogue offers and whether the execution plugin mapped
it; how many orders are working, held back by a pause or a stop, or of
unknown outcome. **Enable trading** on a read-only account. No account's
data.

## Raw records

Under Open and View. What Alpaca answered, kept as raw records, followed from
a fact's or a row's reference, for a person who may read the account; under
Open, **Restore** brings an archived one back for seven days.
