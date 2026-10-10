# Its pages

Both pages are under Manage, for an admin of the plugin. Neither shows a
price: prices are read by the plugins entitled to them. Each page is also a
tool an agent can call through your deployment's MCP connection, answering
exactly what the page shows.

## Connection (`read_connection`)

- **Mode:** live, synthetic (a developer's built-in exchange) or no key.
- **Key:** whether the key ID and the secret key are each set; never their
  values.
- **Entitled / Not entitled:** which of Alpaca's feeds your key answered
  for: IEX in real time, every US exchange (SIP) past 15 minutes, SIP in
  real time.
- **Rate limit:** the plugin spends at most 100 calls a minute, half of
  Alpaca's 200 on the free plan; calls since it started, any answered "too
  many requests" and waited out.
- **Last call, last error.**
- **Stream:** whether it is streaming, how many listings of how many your
  key may stream, since when; reconnects, and trades caught up after one.

## Datasets (`read_datasets`)

- Each dataset: its feed, how its rows arrive, its default terms, whether
  your key is entitled to it, and what it recorded, restated, declined and
  could not convert since the plugin started.
- **Kept current:** the standing wants, searched and paged: for which
  listings, of which dataset, asking what (prices, quotes or trades), and
  whether they are streamed; how many listings the plugin knows and
  streams; days still forming.
