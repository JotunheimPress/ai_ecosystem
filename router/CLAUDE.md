# Backend router

One nginx container that sits in front of text inference (KoboldCPP), so the frontends never
need to know which machine serves them. It is the groundwork for moving text to the AMD Halo box
(`../HALO_PLAN.md`).

## Purpose

- It **owns the name** `koboldcpp` on `ai-net` (network alias) and **host port** `5001`, which that
  container used to publish.
- It forwards each request to the current route. Today that's the local copy,
  **`koboldcpp-local:5001`**, which publishes no host port.
- Frontend URLs are unchanged (`KOBOLD_URL=http://koboldcpp:5001`), and so is `localhost:5001` for
  the dashboard and scripts.

## Structure

| File | What |
|---|---|
| `start.sh` | Runs container `backend-router` from stock `docker.io/library/nginx:1.28-alpine` (pulled by `stack install`). No image of our own. Labels it with a hash of its config; `--check` compares. It also removes the pre-router `koboldcpp` container and the retired Orpheus TTS containers if they exist. |
| `router.conf.template` | The nginx config, rendered by the image's entrypoint (only `${NGINX_LOCAL_RESOLVERS}` is substituted). |
| `$ECO/router/route.conf` | **State, never committed.** One `set $kobold_up …;` line, mounted read-only at `/etc/nginx/route/`. Written only by `stack route`. |

## Running

```bash
stack router                 # or ai-stack/router/start.sh; `stack up` starts it first
stack route                  # show:  text → koboldcpp-local:5001
stack route local            # route to the local copy
stack route halo             # route to HALO_HOST from ai-stack/halo.env (git-ignored)
curl localhost:5001/__router/health   # the router itself (stack's health probe for `router`)
curl localhost:5001/__router/route    # what it is forwarding to right now
stack logs router            # errors only (access log is off)
```

## Gotchas

- **Upstreams are variables resolved per request** through aardvark-dns (`resolver …
  valid=5s`), not `upstream {}` blocks. A static name must resolve when nginx starts, and the local
  containers are often absent, so a static config would refuse to start. While a backend is down,
  requests get **502**.
- **`proxy_buffering off` + 1 h timeouts** keep streamed replies (SSE) streaming. Verified:
  events arrive incrementally through the router.
- **Route changes are graceful.** `nginx -s reload` starts new workers a few hundred ms later, and
  replies already streaming finish on the old route. `stack route` waits until
  `/__router/route` reports the new target before returning.
- **`koboldcpp` health goes through the router**, so `stack up` starts the router
  first. Otherwise a running model reads as down and gets reloaded.
- `HALO_HOST` is validated (`^[A-Za-z0-9.-]+$`) before it reaches `route.conf`, which is nginx
  syntax. Never write free-form input there.
- **Config changes recreate it.** The template is rendered once when nginx starts, so a running
  router keeps the old one. `start.sh` labels the container `ai-stack.config=<hash>` of its run
  flags and template; `stack up` (and `stack router`) recreate it when `start.sh --check` says the
  label doesn't match. `route.conf` isn't hashed: `stack route` reloads it live.
- **Replacing it checks the port first.** If 5001 is held by something that isn't a running
  router, `start.sh` stops before removing anything, so a failed swap can't leave text unrouted.
- **`stack restart router` doesn't stop it first** (start.sh replaces it), and **it stops last** in
  `stack down`.
- **Anything about "the copy on this GPU" must bypass the router**, because through it the request
  could reach the Halo box instead.
