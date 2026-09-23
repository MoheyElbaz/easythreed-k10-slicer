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
```

For each STL it:

1. **Orients it.** It tries the six face-down orientations and keeps the one with
   the most area on the bed, which matters on a cold bed. Override with
   `--rotate-x`, `--rotate-y` or `--no-orient`.
2. **Checks it fits** 100 × 100 × 100.
3. **Sizes the raft to the bed.** The raft margin is 3 mm, and it shrinks for
   wide parts. A part too wide for any raft prints without one, and you're told.
4. **Slices** with the factory profile below.
5. **Audits** the G-code, and deletes it if anything fails:
   - no bed-heating command (`M140`, `M141`, `M190`)
   - every move inside 0–100 mm on X, Y and Z, with no tolerance
   - nozzle temperature inside the printer's 180–230 °C
   - supports present if the part barely touches the bed
   - a filename of plain letters and digits, which is all the K10 reads

## The settings

They come from EasyThreed's own factory profile, the one their Easyware slicer
(a rebranded legacy Cura) embeds in the sample G-code shipped with the printer.

| | value |
|---|---|
| Layer height | 0.2 mm (first layer 0.3) |
| Walls | 2 × 0.4 mm |
| Top / bottom | 3 layers |
| Infill | 20 %, grid |
| Speed | 40 mm/s everywhere, first layer 20, travel 40 |
| Nozzle | 215 °C |
| Bed | none |
| Retraction | 4.5 mm at 40 mm/s (Bowden), no z-hop |
| Fan | 100 % after the first layer |
| Supports | lines, where overhangs are shallower than 30° from horizontal, 15 %, 0.15 mm gap |
| Adhesion | 4-layer raft, 0.22 mm air gap |

Three changes from the factory file, all forced by the machine or the tooling:

- **Raft margin 3 mm, not 5**, so parts up to about 92 mm wide still fit a raft.
- **The priming line runs along the front edge**, where no raft can reach it.
- **Orientation is automatic**, so parts without a flat face don't print into the air.

The profiles are plain OrcaSlicer JSON in `profiles/`, so you can also load
them in the OrcaSlicer app.

## Printing

- Put **one** `.gcode` on the TF card at a time.
- Peel the raft and snap the supports off after printing.
- The K10 is slow by design: 40 mm/s at most, set by its firmware. A 90 mm part
  takes hours.

## Not affiliated

This is an independent tool. It is not made by, endorsed by, or affiliated with
EasyThreed. Use at your own risk.

## Licence

MIT, see `LICENSE`.
