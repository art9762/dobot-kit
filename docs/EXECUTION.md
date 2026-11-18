# Transport and execution contract

This contract describes the classic Magician transport and queue. See
[HARDWARE.md](HARDWARE.md) for the observed behavior on firmware 3.7.0.

## Transport

```python
Transport.request(command, params=b"", *, write=False, queued=False) -> bytes
SerialTransport(port, timeout=1.0, *, allow_writes=False)
```

The default policy permits parameter-free GETs for device status, tool state, tool offsets,
PTP settings and queue index (`0,1,2,5,10,20,60,61,62,63,80,81,82,83,246`).
Explicit `allow_writes=True` enables uint8 IDs, SET and
parameterized GET (for example an input address). It is not a schema validator: the
command backend must validate ID availability, GET/SET support, parameters and queueability.
Queued reads are always rejected. Invalid arguments are rejected before IO; a rejected
argument does not poison an otherwise healthy link.

A transaction deadline includes lock acquisition, partial writes, and reply assembly.
Partial writes continue only the unsent suffix of the same frame. A frame is never resent.
The driver read/write timeouts are reduced to the remaining budget. Any failed exchange,
lock timeout or interruption closes the link, so an orphan ACK cannot become a fresh reply.
Concurrent close detaches the descriptor without waiting for the transaction lock.
Opening and closing send no protocol command; RTS/DTR are set false before port open.

Reply matching checks command, read/write bit, framing, checksum and absence of trailing
frames. GET control is zero, immediate SET control is one with empty parameters. A queued
SET requires exactly eight little-endian bytes and a write bit; control one or three is
accepted. The implementation deliberately does not require an echoed queued bit.

Sources: [Communication Protocol V1.1.5](https://download.dobot.cc/product-manual/dobot-magician/pdf/en/Dobot-Communication-Protocol-V1.1.5.pdf),
control definition on printed page 3, SET response tables (e.g. table 60), queue controls
tables 199–212; and [pinned official DobotLink](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.cpp),
`parseParams`/`decodePTPCmd`. The PDF describes queue-dependent payloads and control fields;
DobotLink dispatches decoders using `c_rw` and extracts the index from eight bytes, without
checking a queued echo. Accepting both queued response controls is our compatibility
interpretation of those sources, not evidence that both occur on the user's firmware.
Reserved bits and a read-bit response to SET are rejected.

## Executor and receipts

```python
CommandExecutor(transport, *, poll_interval=0.02)
executor.read(command, params=b"") -> bytes
executor.submit(command, params=b"", *, queued=True, wait=True, timeout=30.0) -> CommandHandle
executor.current_index() -> int
executor.start()
executor.pause()
executor.clear()
executor.stop(*, clear=True)
executor.close()
handle.wait(timeout=30.0) -> CommandHandle
handle.cancel()
```

The backend must route queue lifecycle operations through the corresponding executor
methods, rather than sending the same opcodes through a raw transport or generic submit.
The executor assumes exclusive ownership of the controller queue for the whole session.
It cannot detect another program that manipulates the queue or reconnect to the same port.

A queued submission reads the current index, sends the action once and retains its
uint64 ACK index. On the first submission, the executor sends START (240) unless the
session has already started the queue. Later submissions append to the running queue.
An acknowledged pause (241) or force-stop (242) makes the next submission send START
again. A standalone clear (245) discards pending commands and preserves the run state.
An explicit `executor.start()` always sends START. All these operations share one IO
lock. Starting the queue also allows work already present on the controller to run.

A handle contains `command`, `queue_index: int | None`, `session_id: str`, and
`state: ExecutionState`. `ExecutionState` is a string enum with `accepted`, `completed`,
`cancelled`, and `invalidated`. ACK means accepted. Polling first reads alarm bits, then
the current index. Only forward, unambiguous index evidence marks a queued handle completed;
this is controller execution evidence, not a measurement of physical position or accuracy.

Immediate writes have no completion index. With `wait=False` they return an accepted handle;
`wait=True` or a subsequent `handle.wait()` raises `OutcomeUnknown(stage="accepted")`.
Backends exposing immediate settings/actions must decide deliberately whether to request
acceptance only. An empty immediate ACK must never be described as completed motion.

All timeout/poll values must be finite and positive. A wait timeout retains an accepted
handle and may be followed by another explicit wait; it sends no replay, stop, or clear.
An alarm raises `RobotAlarmError`, with the active bit numbers in `alarms`, while retaining
the receipt and connection. It does not clear alarms or stop outputs implicitly.

There is no indefinite lock acquisition. Submit and handle waits use their supplied budget;
read has a finite 30-second lock budget; index/lifecycle methods accept an explicit timeout with a 30-second default. The generic Transport
interface has no per-call timeout override: an already running request remains bounded by
the transport's own timeout. Consequently an execution deadline can overrun by at most one
bounded in-flight request (excluding an explicitly nonconforming custom Transport). No
new poll request starts after expiry. SerialTransport enforces its own complete IO deadline.

### Waiting for this session's queued commands

`robot.queue.wait(timeout=...)` snapshots the latest queued action accepted by this
session, serializing with any submission currently using the transport. The executor
keeps its own receipt from the moment the controller acknowledges the action. A timeout
in a convenience call and cancellation of a caller's handle both leave that receipt
available. Waiting again polls the existing index. It never resends the action.
Completion of the snapshot covers its earlier queued actions. A command submitted
after the snapshot belongs to a subsequent wait.

## Stop, cancellation and invalidation

- `handle.cancel()` cancels this handle's waiting permanently. It does not cancel its
  controller command, stop motion, clear the queue, or affect another handle.
- `pause()` sends immediate queue-stop 241. `stop(clear=False)` sends force-stop 242.
  Both wake/cancel current waits, while keeping receipts usable for a later explicit wait.
- `clear()` sends 245. `stop(clear=True)` sends 242 then 245. These invalidate **all** extant
  handles (including completed receipts), rotate `session_id` and reset index tracking.
- `close()` invalidates the session and closes the transport only. Queue execution and
  outputs may continue. The facade must not add implicit stop/clear on context exit.
- Any exchange failure or interruption closes the executor and invalidates its session.
  A new connection requires a new executor. There is no automatic reconnect or replay.

Stop sets a barrier and wakes waiters before waiting for IO. Submissions/start calls that
began before the barrier cannot start the queue after that stop completes; submissions
arriving during the stop are rejected. If stop overlaps an already dispatched start,
force-stop follows it under the IO lock. Stop attempts a controller stop even when a
concurrent submit has already received its action ACK. A subsequent, separately invoked
submit after stop completes is a new explicit action and may start execution again.
`Path.run()` captures the queue generation before validating and sending its waypoints.
A pause, stop or clear interrupts this route even between waypoint submissions or during
a pose or alarm query. The original route cannot start the queue again. Composed
synchronous motion workflows can bind their preflight and submissions to the same
generation with `with robot.motion.sequence():`. Nested scopes inherit the outer
generation. Run an explicitly requested new move outside the interrupted scope.

A failed stop/clear closes the session and reports failure; it never claims successful stop.
Software stop requires a live link and does not establish hardware emergency-stop behavior
or guarantee any tool is disabled.

## Index arithmetic

Indexes remain Python integers and are decoded with `<Q`, never float. Before submission,
`baseline` is sampled. The ACK must be strictly ahead of baseline and the prior submitted
index modulo 2^64, with every distance strictly less than 2^63. Successive observed indexes
must also advance by less than 2^63 (equality is allowed for no progress). Duplicate ACK,
backwards/reset index, and exact half-range ambiguity close/invalidate the session.

Completion requires `target_distance <= observed_distance < 2^63` relative to that handle's
baseline. This admits a genuine wrap through zero without treating an older current index
as completion. A full unseen cycle or a controller reset that happens to masquerade as
forward progress cannot be distinguished by this protocol; sole queue ownership, fewer than
2^63 unseen/outstanding operations, and an uninterrupted controller session are assumptions.

## Errors and outcome evidence

`DobotError` now has `stage` (`outcome` alias), `command`, `queue_index`, and `session_id`.
New errors are `ExecutionTimeout`, `CommandInvalidated`, `RobotAlarmError`, `OutcomeUnknown`.
They are exported from `dobotkit` along with `ExecutionState` and `CommandHandle`.

| Stage | Evidence |
| --- | --- |
| `not_sent` | The write was rejected, the link was closed before send, or its send/lock deadline expired before any write attempt |
| `unknown` | A serial write was attempted but the driver could have transferred an unknown prefix |
| `sent_unconfirmed` | The full frame was written, but a valid matching ACK was not received |
| `accepted` | Valid action ACK was received; execution may still be pending or already finished |
| `completed` | Reserved for explicit completion evidence; normal success is the completed handle |

Errors after queue acceptance describe the **action's** stage even if its subsequent queue
start or index read failed. Lost SET ACK is never reported as proof of non-execution. An
interrupt is re-raised unchanged with stage/command metadata when available; the connection
is closed. Validation `TypeError`/`ValueError` from SerialTransport carry `not_sent` metadata.

## Offline verification

Mock pyserial tests retain bootstrap coverage and add SET flags, parameterized GET, policy
rejection, control-one/control-three queued ACKs, malformed/late/missing ACK, uncertainty
stages, interrupts, and bounded lock contention. Executor doubles cover ACK versus completion,
multiple handles, immediate uncertainty, alarms, uint64 wrap/stale/duplicate/half-range index,
session invalidation, clear/reuse, stop during submission/start, interrupted waits, lost ACK,
and stop failure without replay. These tests provide no USB or hardware acceptance evidence.
