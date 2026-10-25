# samsungframetv-compat

The NickWaterton fork of samsung-tv-ws-api is now published as `samsungframetv`. This package adds
a `samsungtvws` shim, so code that does `import samsungtvws` or
`from samsungtvws.async_art import SamsungTVAsyncArt` keeps working unchanged.

```bash
pip install samsungframetv-compat
```

Don't install it in an environment that also needs the upstream `samsungtvws` package (for example
Home Assistant core), because both provide a `samsungtvws` folder. Change your imports to
`samsungframetv` instead, which is the long term fix.
