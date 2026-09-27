# EasyThreed K10 slicer

Slice any STL for the **EasyThreed K10** with one command, using the printer
maker's own factory settings, and get G-code that has been checked before it
goes on the card.

```bash
python3 k10slice.py part.stl
```

No Easyware, no Windows. It drives [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer)
from the command line on macOS, Windows or Linux. Standard-library Python 3.8+.

## Why

The K10 is a small, cheap printer with three traps that generic profiles fall into:

| trap | what happens |
|---|---|
| **No heated bed.** Its 12 V / 2 A supply can't drive one | A stock Marlin profile sends `M190` ("wait for the bed to heat"), and the printer waits forever |
| **100 × 100 × 100 mm bed** | Stock start G-code primes at X 200, off the side of the bed |
| **It prints the newest file on the TF card**, not the one you choose | An old file on the card gets printed instead of the new one |

This tool refuses to write any G-code that falls into the first two, and
reminds you about the third.

## Install

1. Install [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer/releases) (free).
2. Download this repository.
3. Check everything works:

```bash
python3 k10slice.py --selftest
```

That slices a 20 mm test cube and should print `OK`.

## Use

```bash
python3 k10slice.py part.stl                    # G-code next to the STL
python3 k10slice.py a.stl b.stl --out gcode/    # several at once
python3 k10slice.py part.stl --no-raft          # faster, less grip on the bed
python3 k10slice.py part.stl --no-orient        # slice exactly as modelled
python3 k10slice.py part.stl --rotate-x 180     # force an orientation
python3 k10slice.py part.stl --orca /path/to/OrcaSlicer
python3 k10slice.py part.stl --speed 30 --infill 15   # change any setting
python3 k10slice.py --settings                  # list every setting, its default and range
```

For each STL it:

1. **Orients it.** It tries the six face-down orientations and keeps the one with
   the most area on the bed, which matters on a cold bed. Override with
   `--rotate-x`, `--rotate-y` or `--no-orient`.
2. **Checks it fits** 100 × 100 × 100.
3. **Sizes the raft to the bed.** The raft margin is 3 mm, and it shrinks for
   wide parts. A part too wide for any raft prints without one, and you're told.
4. **Slices** with the defaults below, or the values you pass.
5. **Audits** the G-code, and deletes it if anything fails:
   - no bed-heating command (`M140`, `M141`, `M190`)
   - every move inside 0–100 mm on X, Y and Z, with no tolerance
   - nozzle temperature inside the printer's 180–230 °C
   - supports present if the part barely touches the bed
   - a filename of plain letters and digits, which is all the K10 reads

## The settings

The defaults reproduce the **`Rocket_K10.gcode` sample** EasyThreed ships on the
printer's TF card: its speeds, extrusion, fan and temperatures were read back
from the G-code itself. Every tunable one is a command-line option; values
outside what the K10 can take are refused.

| option | default | range | what it sets |
|---|---|---|---|
| `--speed` | 20 mm/s | 5–40 | every extrusion: walls, infill, top/bottom, bridges, supports |
| `--first-speed` | 20 mm/s | 5–40 | first layer |
| `--layer` | 0.2 mm | 0.05–0.3 | layer height |
| `--first-layer` | 0.3 mm | 0.1–0.35 | first-layer height |
| `--walls` | 2 | 1–6 | wall loops, 0.4 mm each |
| `--top` / `--bottom` | 3 | 0–10 | solid layers |
| `--infill` | 20 % | 0–100 | grid infill |
| `--support-angle` | 30° | 0–90 | support overhangs shallower than this |
| `--support-gap` | 0.25 mm | 0.1–0.4 | air gap between support and part |
| `--raft-layers` | 4 | 1–8 | raft thickness, when a raft is used |
| `--temp` | 215 °C | 180–230 | nozzle |
| `--flow` | 1.1 | 0.8–1.3 | flow ratio (see below) |
| `--fan` | 100 % | 0–100 | part-cooling fan, from layer 2 |
| `--retract` | 4.5 mm | 0–8 | retraction length (Bowden) |
| `--retract-speed` | 40 mm/s | 10–60 | retraction speed |

Fixed by the machine, not options: no heated bed, travel 40 mm/s (the firmware
cap), no z-hop, a 0.22 mm raft air gap.

**Supports are set to snap off.** A 0.15 mm gap with three dense interface layers
and the 1.1 flow welds PLA supports to a curved surface. The defaults leave one
full layer of air (0.25 mm, rounded to the layer), two interface layers at 0.8 mm
spacing, 0.9 flow on support and interface, and 0.8 mm side clearance.

**Why 20 mm/s and not the manual's 40.** 40 mm/s is the firmware's ceiling. The
factory sample prints every wall and support at 20 mm/s, which gives cleaner walls,
better layer bonding and neater overhangs on this light, bed-less machine. It
costs about 40 % more print time; `--speed 40` is there when time matters more.

**Why flow 1.1.** The factory G-code extrudes a full width × height rectangle per
line (0.080 mm² for 0.4 × 0.2). OrcaSlicer's rounded-line model extrudes about
11 % less, so 1.1 brings it back to the factory amount (0.081 mm²).

**Also matched to the factory file:** absolute extrusion (`M82`), the fan held
on instead of switching per feature, and no acceleration, jerk or progress
commands (`M201`–`M205`, `M73`), which the factory file never sends.

Three changes from the factory file, all forced by the machine or the tooling:

- **Raft margin 3 mm, not 5**, so parts up to about 92 mm wide still fit a raft.
- **The priming line runs along the front edge**, where no raft can reach it.
- **Orientation is automatic**, so parts without a flat face don't print into the air.

The profiles are plain OrcaSlicer JSON in `profiles/`, so you can also load
them in the OrcaSlicer app.

## Printing

- Put **one** `.gcode` on the TF card at a time.
- Peel the raft and snap the supports off after printing.
- The K10 is slow by design: 40 mm/s at most, set by its firmware, and the
  default is 20 for quality. A 90 mm part takes hours.

## Not affiliated

This is an independent tool. It is not made by, endorsed by, or affiliated with
EasyThreed. Use at your own risk.

## Licence

MIT, see `LICENSE`.
