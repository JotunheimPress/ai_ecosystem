# Backend router

One nginx container that sits in front of text inference (KoboldCPP) and Pantheon's voice
(Orpheus TTS), so the frontends never need to know which machine serves them. It is the
groundwork for moving those backends to the AMD Halo box with the 4070 as fallback
(`../HALO_PLAN.md`).

## Purpose

- It **owns the names** `koboldcpp` and `orpheus-tts` on `ai-net` (network aliases) and the
  **host ports** those containers used to publish: `5001` on all interfaces and `127.0.0.1:5005`.
- It forwards each request to the current route. Today that's the local copies,
  **`koboldcpp-local:5001`** and **`orpheus-tts-local:5005`**, which publish no host ports.
- Frontend URLs are unchanged (`KOBOLD_URL=http://koboldcpp:5001`,
  `ORPHEUS_URL=http://orpheus-tts:5005`), and so is `localhost:5001` for the dashboard and scripts.

## Structure

| File | What |
|---|---|
| `start.sh` | Runs container `backend-router` from stock `docker.io/library/nginx:1.28-alpine`. No image of our own. It also removes the pre-router `koboldcpp` / `orpheus-tts` containers if they exist. |
| `router.conf.template` | The nginx config, rendered by the image's entrypoint (only `${NGINX_LOCAL_RESOLVERS}` is substituted). |
| `$ECO/router/route.conf` | **State, never committed.** Two `set $kobold_up …; set $orpheus_up …;` lines, mounted read-only at `/etc/nginx/route/`. Written only by `stack route`. |

## Running

```bash
stack router                 # or ai-stack/router/start.sh; `stack up` starts it first
stack route                  # show:  text → koboldcpp-local:5001 / voice → orpheus-tts-local:5005
stack route local            # route to the 4070 copies
stack route halo             # route to HALO_HOST from ai-stack/halo.env (git-ignored)
curl localhost:5001/__router/health   # the router itself (stack's health probe for `router`)
curl localhost:5001/__router/route    # what it is forwarding to right now
stack logs router            # errors only (access log is off)
```

## Gotchas

- **Upstreams are variables resolved per request** through aardvark-dns (`resolver …
  valid=5s`), not `upstream {}` blocks. A static name must resolve when nginx starts, and the local
  containers are often absent, so a static config would refuse to start. While a backend is down,
  requests get **502**. Pantheon's Orpheus state check treats that the same as a refused connection.
- **`proxy_buffering off` + 1 h timeouts** keep streamed replies (SSE) streaming. Verified:
  events arrive incrementally through the router.
- **Route changes are graceful.** `nginx -s reload` starts new workers a few hundred ms later, and
  replies already streaming finish on the old route. `stack route` waits until
  `/__router/route` reports the new target before returning.
- **`koboldcpp` / `orpheus-tts` health goes through the router**, so `stack up` starts the router
  first. Otherwise a running model reads as down and gets reloaded.
- `HALO_HOST` is validated (`^[A-Za-z0-9.-]+$`) before it reaches `route.conf`, which is nginx
  syntax. Never write free-form input there.
- **Anything about "the copy on this GPU" must bypass the router.** `koboldcpp/start.sh` unloads
  Orpheus with `podman exec orpheus-tts-local curl …`, because through the router it could unload
  the Halo box's copy instead.
