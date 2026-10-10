# Setting it up

You need an Alpaca account's API key. A free account's key works, and so
does a paper account's; it does not expire.

1. **Get a key.** In Alpaca's dashboard, under API keys, generate one. Copy
   the secret key: Alpaca shows it once.
2. **Add the plugin** to your deployment and approve what it asks for: the
   `dgm` role, and storage for Alpaca's responses.
3. **Enter the key.** Open the plugin under Manage, then Settings, and enter
   the API key ID and the API secret key. Both are secret: once saved they are
   never shown again.
4. **Check the connection.** The Connection page should say Connected, with
   IEX in real time and SIP past 15 minutes entitled on a free account.
5. **License and entitle.** On the deployment's Data sources page, license
   `alpaca:daily` and `alpaca:live`. Each starts from Alpaca's terms: one
   person's use. Then entitle the plugins that read prices to each dataset.

Nothing is read from Alpaca until a plugin entitled to a dataset asks for a
price.
