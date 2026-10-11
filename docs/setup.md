# Setting it up

You need an Alpaca account's API key. A free account's key works, and so
does a paper account's; it does not expire.

1. **Get a key.** In Alpaca's dashboard, under API keys, generate one. Copy
   the secret key: Alpaca shows it once.
2. **Add the plugin** to your deployment and approve what it asks for: the
   `data` role (a data plugin), and storage for Alpaca's responses.
3. **Enter the key.** Open the plugin under Manage, then Settings, and enter
   the API key ID and the API secret key. Both are secret: once saved they are
   never shown again.
4. **Check the connection.** The plugin's Summary says Answering, and its
   Connection page lists IEX in real time and SIP past 15 minutes as what
   Alpaca serves a free account's key.
5. **License and entitle.** On the deployment's Data sources page, license
   `alpaca:daily`, `alpaca:live` and `alpaca:trades`. Each starts from
   Alpaca's terms: one person's use. Then entitle the plugins that read
   prices to each dataset. The plugin's Datasets page then says each is
   licensed.

Nothing is read from Alpaca until a plugin entitled to a dataset asks for a
price.
