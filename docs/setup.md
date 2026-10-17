# Setting it up

Each step by an admin of the plugin, on its roles as each says. The plugin
must already be launched, its roles approved by a deployment admin. The
steps follow the data and custody roles' first-use paths; what is Alpaca's own
is below.

## 1. The API key

In Alpaca's dashboard, under API keys, generate a key: the paper account's to trade with simulated money, the live account's to trade for real. Alpaca shows the secret key once.

## 2. Enter it

**Manage → Settings.**

1. **Environment**, first: **Sandbox** is Alpaca's paper trading API, simulated money; **Live** is its live Trading API. It must match the key. It is Sandbox until a person chooses Live.
2. The **API key ID** and the **API secret key**. Secret settings: once saved
   they are never shown again, logged, put on a page or sent anywhere but
   Alpaca.
3. **Read every** and **Stale after**: how often the account is read (300
   seconds by default), and how long it may go unanswered before it is
   reported stale (36 hours).

**Connection** then says under Key whether each part is set, never its
value, and whether Alpaca answered. With none, "No key yet", linking to
Settings.

## 3. License and entitle its datasets

A deployment admin, on the deployment's **Data sources** page, licenses
`daily`, `live` and `trades`, each starting from Alpaca's terms, one person's
use, and entitles the plugins that read prices, trades and quotes. Nothing
is read from Alpaca until a plugin entitled to a dataset asks for a price.

## 4. Link the account

A deployment admin grants the plugin the accounts it may link to. Its admin
links Alpaca's account to one of them on **Account links**, under Manage, or a
deployment admin creates a new account and links it in one step. Then a
deployment admin grants people read or write on it, so they see its
statements under View or Open.

## 5. Trading, only if you place orders through it

A deployment admin makes the plugin available to the desk's execution
plugin, whose admin links it and maps a book account to the account's
venue account code, shown on **Trading**. A new account is read-only:
every order on it is rejected as unsupported, nothing sent, until the
connectivity role's admin presses **Enable trading** on Trading. Its
catalogue is then published: `market` and `limit`, each day, GTC, IOC or FOK; `stop` and `stop_limit`, each day or GTC. Alpaca takes no good-till-date order.

## Sample data

On a development deployment, **Sample data** answers from built-in made-up
data instead of Alpaca, and trades with a simulated brokerage. Nothing reaches
Alpaca.
