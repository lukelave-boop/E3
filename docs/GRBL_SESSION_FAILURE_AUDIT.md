# Primary GRBL session failure audit

This records the pre-change failure mechanisms that motivated the controller
session hardening. It is an architecture record, not proof of physical
verification. The corrected behavior is automated-test evidence until the
supervised validation run is recorded.

## Observed failure chain

The physical STOP correctly invalidated command trust, but the old design left
the transport publicly connected while separately setting reconnect-required.
The desktop therefore projected the Pi/network connection as **ONLINE** at the
same time that controller commands were forbidden. Reconnect was implemented as
separate disconnect and connect work, and polling, STOP, another client, or a
stale worker could interleave between them. A later Home used a reply stream
whose byte/transaction boundary had not been proven; its `$#` query timed out,
while ordinary primary TX/RX evidence remained debug-only.

## Pre-change mechanisms

| Area | Failure mechanism | Consequence |
|---|---|---|
| Publication | `_connected` and the shared transport were published before startup synchronization, identity, safe-output normalization, and all required queries completed. | UI/operations could observe a half-initialized descriptor as usable. |
| STOP state | STOP set reconnect-required without making the old descriptor permanently inaccessible through one authoritative state. | `connected` and reconnect-required could both be true; Connect could return early or issue circular disconnect/reconnect guidance. |
| Replacement | Reconnect was a disconnect call followed by a separate connect call. | Status polling, another client, or STOP could act between the two side effects. |
| Transport ownership | Workers resolved mutable global `_transport` state after they began, and cleanup was not consistently bound to the object/generation it owned. | An old worker could read, write, close, or publish state against a replacement session. |
| Input synchronization | Startup used a fixed settle plus line-queue drain. It did not atomically clear kernel RX, a partial unterminated reader buffer, or bytes arriving just after the drain, and it did not prove a quiet boundary. | Startup fragments or prior replies could concatenate with, or be consumed as, the first new transaction. |
| Transactions | A command write and acknowledgement wait could re-resolve different shared transport state. Realtime status, startup chatter, malformed input, payload, and terminal lines lacked one strict ownership boundary. | One operation could consume another operation's payload/ACK; a delayed `ok` could shift later `$G`/`$#` ownership. |
| Ambiguity | Timeout/read/write uncertainty did not uniformly make the entire session terminal. | Retrying or continuing on the same untagged GRBL stream could accept a late response as a later command's ACK. |
| Coordinate trust | Home/coordinate publication was tied chiefly to STOP epoch rather than the exact controller session object and generation. | A stale callback/finalizer could publish motion-ready after replacement. |
| Pi causality | Controller mutations did not require the last observed Pi boot ID and controller-session generation, and simultaneous lifecycle calls did not share one result. | A stale Disconnect/Connect/Home request could race a later STOP or recovered session; generation reset on Pi restart was ambiguous. |
| Desktop projection | Reachable Pi, open transport, synchronized controller, Home-required, and motion-ready were compressed into loosely related booleans. | Contradictory **ONLINE + RECONNECT REQUIRED** and stale action enablement were possible. |
| Diagnostics | High-volume primary TX/RX was DEBUG-only and failure status lacked stable session/transaction fields. | A failed Windows Connect/Home left insufficient bounded Pi journal evidence. |

## Historical constraints retained

The audit inspected earlier fixes and preserved their intent:

- `d65bec4` serialized Connect/Home reply-stream ownership;
- `bdee965` narrowly handled exact pre-home GRBL `error:9`;
- `09d9a8d` bound reconnect work to the STOP generation;
- `b391e8a` accepted only verified active-homing to Idle evidence;
- `6c03f03` used a fresh transport for permitted initial retry;
- `c8430eb` kept transport mechanics separate from controller dialect policy;
- `3f3dbab` retained Pi-owned execution and non-destructive observer detach.

The parent `bb68b957205aced7cee15b003c7e68d207e87b2d` secondary
Air-Assist framing fix is the base of this work and remains part of the resulting
history. Its exact `M106 S255` / `M106 S0` mapping and durable OFF recovery are
unchanged.

## Corrected ownership boundary

A transport is now private until a complete, non-motion handshake succeeds.
Every command and Home transaction is bound to one immutable session generation
and sequence. Any write/read/timeout/cancellation/framing ambiguity revokes
authority, attempts existing fail-off behavior, closes that exact transport,
and permits only bounded fresh-session recovery. A fully consumed,
grammar-valid controller rejection is distinguished from ambiguity. Pi control
requests carry boot/session compare-and-swap metadata, and the desktop binds
queued work and callbacks to the same causality tuple.

See [GRBL session recovery validation](GRBL_SESSION_RECOVERY_VALIDATION.md) for
the later physical acceptance procedure.
