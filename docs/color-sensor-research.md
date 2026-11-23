# Classic Magician color sensor: verified wire contract

Verified 2026-11-20 against Dobot-owned source and a first-party compatibility article. No hardware or serial ports were accessed.

## Implementation contract

**High confidence:** command ID **137 / `0x89`** is shared by `SetColorSensor` and `GetColorSensor`. The official DobotLink implementation writes exactly three parameter bytes: `enable, port, version`. Its getter sends no parameter bytes. [1]

For a directly connected classic Magician, with routing/reserved bits zero:

| Operation | ID | Ctrl | Parameter bytes | Expected response parameters |
|---|---|---|---|---|
| Enable/disable, immediate | `89` | `01` | `enable, port, version` (three `uint8`) | No setter configuration fields defined; decoder accepts an empty parameter section |
| Read RGB, immediate | `89` | `00` | Empty; **no port or version byte** | Exactly `r, g, b`, three unsigned bytes |

Ctrl bit 0 is read/write (`0`/`1`); bit 1 selects queued execution. Keep this API immediate (`isQueued=false`). Generic DobotLink framing can produce a queued setter (`ctrl=03`), and its color decoder recognizes eight-byte queue-index replies, but the high-level SDK color setter has no queue argument. Firmware support for queued color configuration is therefore not established here. An acknowledgment is not evidence of successful physical sensing. [1][2][4]

Frame: `AA AA LEN ID CTRL PARAMS CHECKSUM`, where `LEN = 2 + len(PARAMS)` and `CHECKSUM = -(ID + CTRL + sum(PARAMS)) & 0xff`. Header and length are excluded from the checksum. [2]

Derived byte fixtures, **not hardware captures**:

```text
Enable V2 sensor on GP2: AA AA 05 89 01 01 01 01 73
Disable same sensor:    AA AA 05 89 01 00 01 01 74
Read RGB:               AA AA 02 89 00 77
Empty setter ACK form:  AA AA 02 89 01 76
Read RGB reply:         AA AA 05 89 00 RR GG BB CC
                         CC = -(0x89 + RR + GG + BB) & 0xff
```

## Interface and sensor-version options

- `enable`: `0` disabled, `1` enabled. [1][5]
- Wire `port`: **GP1 = 0, GP2 = 1, GP4 = 2, GP5 = 3**, derived from the SDK's implicit C enum values. These are enum ordinals, **not** connector numbers `1, 2, 4, 5`. [3]
- `version`: **0 = sensor V1; 1 = sensor V2**. This identifies the peripheral, not robot hardware or firmware. No automatic version detection/default is established by these sources. [5]
- Each RGB component is `uint8`, range `0..255`; there is no port echo, alpha, clear channel, or 16-bit RGB in this response. DobotLink's `ColorStruct` confirms the order and exact length. This establishes representation, **not calibrated/raw radiometric intensity semantics**. [1][4][6]

## Compatibility and caveats

- The official ROS SDK declares the older `SetColorSensor(bool, ColorPort)` without `version`; Dobot's compatibility article documents the newer three-argument API. The old API declaration alone does **not prove an older two-byte wire layout**. Precise firmware cutoff, old-packet layout, and backward compatibility were **not established**; do not guess a fallback or retry a timed-out write with a different layout. [4][5]
- DobotLink explicitly appends each setter field as one `quint8`; do not serialize `ColorPort` using the host C enum's storage width. [1]
- Dobot's compatibility table restricts a **V1 color sensor on a V2 Magician to GP2**; a V2 sensor on a V1 Magician lists GP1/GP2/GP4/GP5. Thus enum membership alone does not guarantee a supported physical pairing. The same article distinguishes V1 I/O-based recognition from V2 communication-based recognition. [5]
- Do not confuse this command with DobotLink's separate **Seeed color sensor command 213**, whose response has different fields/widths. [1][6]
- Missing/disabled/disconnected-sensor values, acquisition delay, measurement freshness, and physical color accuracy are not verified here. Do not interpret a valid ACK or zero RGB as proof of a working sensor.
- Web browsing was attempted as requested but returned no usable rendered results. GitHub API/raw HTTPS and the official article were retrieved directly. Official protocol/API PDFs on `download.dobot.cc` timed out; no PDF version numbers, section numbers, or claims from those unread documents are treated as verified.

## Primary sources

1. [DobotLink encode/decode source, pinned](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.cpp#L5317-L5343): setter/getter encoding; decoder lines 2136–2158; Seeed encoding lines 5929–5947.
2. [DobotLink packet framing, pinned](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DP2Packet.cpp#L59-L75): Ctrl; checksum lines 245–254; frame lines 288–299.
3. [Official Magician ROS SDK ColorPort enum, pinned](https://github.com/Dobot-Arm/Magician_ROS/blob/0bb0dc082596ebce4860212b21068a5c2210b096/src/dobot/src/DobotDll_x64/DobotType.h#L441-L446).
4. [Official ROS SDK C API, pinned](https://github.com/Dobot-Arm/Magician_ROS/blob/0bb0dc082596ebce4860212b21068a5c2210b096/src/dobot/src/DobotDll_x64/DobotDll.h#L150-L154): legacy setter and `GetColorSensor(uint8_t *r, uint8_t *g, uint8_t *b)`.
5. [Dobot V1/V2 comparison and peripheral compatibility](https://www.dobot-robots.com/insights/news/new-update-to-dobot-magician-comparison-between-v1-and-v2.html), §§3.3, 4.1.3, 4.2, especially table 4.6. Its color enum labels contain an `IF_` naming typo; use the SDK's `CL_` declarations.
6. [DobotLink response structures, pinned](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.h#L593-L605).

Confidence: **high** for the DobotLink packet layouts, port ordinals, version flags, and RGB representation (official source plus first-party compatibility/API article); **unverified** for the connected unit's firmware compatibility and actual sensor behavior.
