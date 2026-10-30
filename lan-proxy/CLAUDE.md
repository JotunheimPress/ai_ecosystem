# LAN proxy — the apps on other devices

One **Caddy** container (`lan-proxy`, stock `docker.io/library/caddy:2.11.4`) on `ai-net` that
is the stack's only door to the local network. Every app stays published on `127.0.0.1`; this
container reaches them by name and serves each one over **HTTPS** with a certificate from its
own local CA. **Off by default.** `stack lan on` turns it on.

| LAN address | App | Upstream | Who checks |
|---|---|---|---|
| `http://NAME:8780/` | start page + CA certificate | (files) | nobody: it holds only links and the public root cert |
| `https://NAME:8761/` | Pantheon | `companion:7861` | Pantheon's own accounts |
| `https://NAME:8760/` | Rhapsode | `ai-story-maker:7860` | proxy password (user `stack`) |
| `https://NAME:8762/` | Iguana Studio | `iguana:7862` | proxy password |
| `https://NAME:8764/` | Nocturne | `nocturne:7864` | proxy password |

The AI Ecosystem dashboard (`:7863`) is **not** served: it starts and stops services and has no
login (`stack-ui/CLAUDE.md`).

## Running

```bash
stack lan                     # on/off, the addresses, the password, the CA fingerprint
stack lan on [NAME]           # NAME defaults to <hostname>.local (avahi announces it)
stack lan on 192.168.1.50    # for devices that can't resolve .local (some Android)
stack lan off                 # proxy removed, Pantheon + Iguana back to loopback-only
stack lan password            # make a new proxy password
LAN_PROXY_BIND=127.0.0.1 lan-proxy/start.sh NAME   # dry run: loopback-only ports
```

`stack lan on|off` writes `ai-stack/lan.env` (git-ignored: `LAN=on|off`, `LAN_NAME=`). `stack`
reads it at the top of every run and exports `PANTHEON_PUBLIC_ORIGIN=https://NAME:8761` and
`IGUANA_ALLOWED_HOSTS=NAME`, so a restart from the CLI, from `stack up` at boot or from the dashboard
keeps the apps in the same mode. Turning it on or off restarts Pantheon and Iguana if they're up.
`stack up` starts the proxy too while LAN is on.

State (never committed) is **`$ECO/lan-proxy/`**: `data/` (Caddy's CA: `pki/authorities/local/root.crt`
+ **`root.key`**, and the site certs), `config/`, `Caddyfile` and `www/index.html` (both regenerated on
every start, from validated values only), `password` (0600) and `password.hash` (bcrypt).

## Why it is built this way

- **Pantheon needs HTTPS, not just a reachable port.** pytincture allows its plain-HTTP login only
  from a literal loopback client and Host, and refuses to trust proxy headers in that mode. In LAN
  mode Pantheon runs pytincture's production login instead: password only, exact `allowed_hosts`
  (`127.0.0.1` + NAME), canonical origin `https://NAME:8761`, `Secure` `__Host-` cookies, forwarded
  headers trusted (`companion/service.py` `_network_settings`). There is **one** canonical origin, so
  in LAN mode Pantheon is used at the HTTPS address on this computer too; `http://127.0.0.1:7861`
  redirects there (except `/api/health`, which `stack` probes). Browsers also allow the
  microphone only on HTTPS pages.
- **One port per app, not paths.** The apps assume they sit at `/` (pytincture's `/{application}`
  routes, absolute `/static` URLs).
- **The password for the apps without a login** is Caddy `basic_auth`, sent only inside TLS.
  Iguana's pytincture Host check also has to admit NAME, hence `IGUANA_ALLOWED_HOSTS`.
- **Rhapsode and Nocturne publish on `127.0.0.1` now** (they used to be on all interfaces with no
  login), so the proxy is the only way in from the LAN.
- **The CA page is plain HTTP** because a device can't check HTTPS before it trusts the CA. Compare
  the SHA-256 fingerprint `stack lan` prints. Only `root.crt` is reachable through a fixed rewrite;
  the pki directory itself (with `root.key`) is never a file-server root.
- Ports publish on **all interfaces** even where `host.env` sets `BIND=127.0.0.1` (the Halo box):
  this container is the one thing meant to be reachable. On a host with a firewall that's closed by
  default (Rex/Debian), open 8760–8764 and 8780. Fedora Workstation's zone already allows them.

## Verified 2026-10-30 (dry run, loopback-bound)

Through the proxy with its CA: Rhapsode 401 without and with a wrong password, 200 with it;
Iguana and Nocturne 200; certificate SAN `DNS:myhost.local`, issuer `AI Stack LAN - ECC
Intermediate`; `/root.key` and `..` paths 404. Pantheon (a throwaway instance with a seeded admin):
login page 200, wrong password 401, right password 303, then `__Host-pantheon-session` + csrf cookies,
both `Secure`, and the signed-in page 200. Loopback mode afterwards unchanged.
Live the same day (`stack lan on`, all interfaces): every address answers by its mDNS name and
via 192.168.1.50 (401s for the locked apps until the password is given). The first real start had
exited silently: the password pipe (`tr </dev/urandom | head`) dies of SIGPIPE, which `set -e` +
`pipefail` turn into an exit — the dry runs never hit it because the password file already existed.
**Not yet tried from a phone**, and the desktop avatar (`stack desktop`) in LAN mode is untested:
it loads `127.0.0.1:7861/desktop`, which redirects to the HTTPS address, so its browser has to trust
the CA.
