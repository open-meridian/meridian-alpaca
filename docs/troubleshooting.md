# Troubleshooting

| You see | It means | Do |
|---|---|---|
| Connection: **No key** | One part of the key is missing | Enter both parts in Settings |
| Connection: **Not answering** | Alpaca refused the key | Check the key in Alpaca's dashboard, or generate a new one and enter it |
| SIP past 15 minutes **not entitled** | Alpaca refused consolidated history for the key | Daily prices cannot be served; contact Alpaca about the account |
| Datasets: **declined** grows | A reader asked for a listing the plugin cannot name to Alpaca, or a day with no session | See the Datasets page's note; a weekend or holiday has no close |
| Datasets: **unconverted** grows | Alpaca sent a value the contract cannot hold exactly | Nothing is recorded for it; the raw response is kept |
| A live price with **no venue** | Your deployment does not hold the exchange Alpaca named yet | It is reported to the platform, and named once the venue is held |
| **Last error** names a 429 | Alpaca's rate limit was hit and waited out | Nothing; the plugin spends half the limit |
| Stream: **Refused** | Alpaca refused the key, the plan (SIP without Algo Trader Plus) or a second connection | Check the key; close another program streaming with the same key |
| Standing wants **not streamed** | More listings are wanted than a free account may stream (30) | Fewer at once, or Algo Trader Plus |
| Datasets: trades **unconverted** grows | Alpaca sent a condition code the plugin has no conversion for | Those trades' eligibilities are not known; the code is kept as reported |
