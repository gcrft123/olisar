# Bundled binaries

Holds the **`olisar-funnel`** helper — a small Go binary (built from
`desktop/funnel-sidecar/`, using Tailscale's `tsnet` library) that powers remote access:
it joins the operator's tailnet from a pasted auth key, turns on Tailscale Funnel, and
reverse-proxies the public `https://…ts.net` URL to Olisar's local port. No domain needed,
no `tailscaled` daemon.

`electron-builder` copies it into the app's resources, and the Electron main process passes
its path to the backend via `OLISAR_FUNNEL`. The backend (not Electron) runs it so the auth
key never leaves the backend.

## Rebuilding the sidecar

```sh
cd desktop/funnel-sidecar
GOOS=darwin  GOARCH=arm64 go build -ldflags="-s -w" -o ../resources/olisar-funnel .
# Windows (run on/for Windows before building the .exe installer):
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o ../resources/olisar-funnel.exe .
```

If the helper is absent, the app still runs locally; the remote-access toggle just reports
that the helper isn't bundled.
