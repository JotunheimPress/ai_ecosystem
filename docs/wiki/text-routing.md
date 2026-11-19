# Text on another machine

[Wiki home](README.md) · User guide

The router can send **text** (KoboldCPP) to a KoboldCPP on another machine, the AMD Halo box,
while image, sound and decisions stay local. Frontends notice nothing: they keep using
`http://koboldcpp:5001`.

## Setting it up

Put the other machine's address in `ai-stack/halo.env` (git-ignored):

```
HALO_HOST=halo.lan
```

Only letters, digits, dots and hyphens are accepted.

## Switching

```bash
stack route            # show where text goes now
stack route halo       # send text to HALO_HOST
stack route local      # back to this machine's copy
```

In the dashboard, the router card has **Route text to Halo** / **Serve text locally**. A switch is
graceful: replies already streaming finish on the old route.

## What changes while text is routed away

- `stack up` skips starting the local KoboldCPP.
- The text card shows the other machine's model; its buttons say "local copy".
- Choosing a local model offers no restart: restarting the local copy can't change what the
  other machine serves.

Background and the full Halo plan: [`HALO_PLAN.md`](../../HALO_PLAN.md),
[`router/CLAUDE.md`](../../router/CLAUDE.md).
