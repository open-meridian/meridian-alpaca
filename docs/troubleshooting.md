# Troubleshooting

## Market data

| You see | It means | Do |
|---|---|---|
| Connection: **No key yet** | One part of the key is missing | Enter both parts in Settings (the link) |
| Connection: **Not answering** | Alpaca refused the key | Check the key in Alpaca's dashboard, or generate a new one and enter it |
| SIP past 15 minutes **not entitled** | Alpaca refused consolidated history for the key | Daily prices cannot be served; contact Alpaca about the account |
| Datasets: **declined** grows | A reader asked for a listing the plugin cannot name to Alpaca, or a day with no session | See the Datasets page's note; a weekend or holiday has no close |
| Datasets: **unconverted** grows | Alpaca sent a value the contract cannot hold exactly | Nothing is recorded for it; the raw response is kept |
| A live price with **no venue** | Your deployment does not hold the exchange Alpaca named yet | It is reported to the platform, and named once the venue is held |
| **Last error** names a 429 | Alpaca's rate limit was hit and waited out | Nothing; the plugin spends half the limit |
| Stream: **Refused** | Alpaca refused the key, the plan (SIP without Algo Trader Plus) or a second connection | Check the key; close another program streaming with the same key |
| Wants: **Past the stream's limit** | More listings are wanted than a free account may stream (30) | Fewer at once, or Algo Trader Plus |
| Datasets: trades **unconverted** grows | Alpaca sent a condition code the plugin has no conversion for | Those trades' eligibilities are not known; the code is kept as reported |
| Datasets: **Not licensed yet** | No deployment admin has licensed the dataset | Ask a deployment admin to license it on Data sources; the page says so within a minute |
| Kept current: subjects **declined today** | Alpaca has nothing for a listing (a plan fund a custodian names), or its record names no symbol | Nothing; each is declined once for the date, and asked again the next day |

## The account

| You see | It means | Do |
|---|---|---|
| Statements: **needs sign-in** | Alpaca refused the API key | Check it at Alpaca, or make a new one and enter it in Settings |
| Statements: **disabled** | Alpaca says the account is closed or blocked | Raise it with Alpaca |
| Statements: **stale** | Alpaca has not answered for longer than Stale after | Nothing, usually: the next read catches up |
| An account readers cannot see | It is not linked, or they hold no grant on the account it is linked to | Link it on Account links; a deployment admin grants read or write |

## Orders

| You see | It means | Do |
|---|---|---|
| Every order rejected **unsupported** | Trading is not enabled on the account | The connectivity role's admin enables it on Trading |
| An order type or time in force rejected | Alpaca's catalogue does not offer it | Choose one the catalogue lists on Trading |
| A fill waits | It is sent only with its settlement date, from the deployment's date read | Nothing: it is asked about again on each read |
| A fill on no order | An order placed in Alpaca's own app filled | The desk accepts it to an order or answers it DK |
| Orders **held** | A pause or a stop is in force | Lift it; a stop also cancelled what was working |
