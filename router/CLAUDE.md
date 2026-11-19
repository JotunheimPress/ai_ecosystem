# Backend router

One nginx container in front of every capability the stack provides, so the frontends never
need to know which copy serves them. Text can also move to the AMD Halo box (`../HALO_PLAN.md`).

## Purpose

| Capability | Name on ai-net (alias) | Host port | Forwards to |
|---|---|---|---|
| Text | `koboldcpp:5001` | `5001` (BIND) | `$kobold_up` from `route.conf`: `koboldcpp-local:5001` or the Halo box |
| Image and sound | `comfyui:8188` | `HOST_COMFY_PORT` (8188, or 8189 in host.env) | `comfyui-local:8188` |
| Decision | `fortuna:8009` | `127.0.0.1:8009` | `fortuna-local:8009` |

- It **owns the names and host ports** those containers used to have (since 2026-11-19 for ComfyUI
  and Fortuna). The `*-local` containers publish no host port.
- Frontend URLs are unchanged (`http://koboldcpp:5001`, `http://comfyui:8188`,
  `http://fortuna:8009`), and so are the host ports the dashboard and scripts use.
- Only text is routable. Image and decision upstreams are fixed in the template, so `route.conf`
  holds one variable.

## Structure

| File | What |
|---|---|
| `start.sh` | Runs container `backend-router` from stock `docker.io/library/nginx:1.28-alpine` (pulled by `stack install`). No image of our own. Labels it with a hash of its config; `--check` compares. It also removes, by exact name, the pre-router `koboldcpp`, `comfyui` and `fortuna` containers and the retired Orpheus TTS ones (never `comfyui-8188`, which is the host OS's). |
| `router.conf.template` | The nginx config, rendered by the image's entrypoint (only `${NGINX_LOCAL_RESOLVERS}` is substituted). |
| `$ECO/router/route.conf` | **State, never committed.** One `set $kobold_up …;` line, mounted read-only at `/etc/nginx/route/`. Written only by `stack route`. |

## Running

```bash
stack router                 # or ai-stack/router/start.sh; `stack up` starts it first
stack route                  # show:  text → koboldcpp-local:5001
stack route local            # route to the local copy
stack route halo             # route to HALO_HOST from ai-stack/halo.env (git-ignored)
curl localhost:5001/__router/health   # the router itself (stack's health probe for `router`)
curl localhost:5001/__router/route    # what each capability forwards to right now
curl localhost:5001/__router/capabilities   # the same as JSON (address + upstream)
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
- **ComfyUI's listener is set up for ComfyUI**: WebSocket upgrade for `/ws` (job progress),
  `Host $http_host` (ComfyUI compares Host with Origin, so the port must survive), and
  `client_max_body_size 0` so ComfyUI's own upload limit applies. Verified on a scratch network:
  WebSocket 101, 70 MB upload, encoded `/view?filename=` query intact.
- **An absent backend is a fast 502** (`resolver_timeout 2s`; measured 1.7 s), not a hang.
- **Rollback** (to the per-backend containers): revert the change, then
  `podman rm -f backend-router comfyui-local fortuna-local`, delete `$ECO/router/route.conf`, and
  `stack up`.
- **Anything about "the copy on this GPU" must bypass the router**, because through it the request
  could reach the Halo box instead.
