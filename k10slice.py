#!/usr/bin/env python3
"""
k10slice - slice any STL for the EasyThreed K10, safely, with one command.

    python3 k10slice.py part.stl
    python3 k10slice.py a.stl b.stl --out ./gcode
    python3 k10slice.py part.stl --rotate-x 180        # force an orientation
    python3 k10slice.py --selftest                     # check your install

It drives OrcaSlicer from the command line with a profile built from
EasyThreed's own factory settings, then AUDITS every file before you put it on
the card. Standard library only; Python 3.8+.

WHY THIS EXISTS. The K10 has no heated bed - its 12 V / 2 A supply could not
drive one. Generic Marlin profiles send `M190` (wait for the bed to heat) and
the printer waits forever. They also prime at X 200 on a 100 mm bed. And the
K10 prints the NEWEST file on the TF card, not one you choose. Every one of
those is a silent failure; this script makes each one a loud error instead.

WHAT IT DOES, per STL
  1. Orients it. Tries the six face-down orientations and keeps the one with the
     most area touching the bed. --rotate-x / --rotate-y / --no-orient override.
  2. Checks it fits 100 x 100 x 100.
  3. Sizes the raft to the bed. The factory raft margin is 3 mm; a part too wide
     for that gets a narrower margin, and one too wide for any raft gets none.
  4. Slices with OrcaSlicer: 0.2 mm layers, 40 mm/s, 215 C, 4.5 mm retraction,
     line supports, raft - the factory profile.
  5. Audits the G-code, and deletes it if any check fails:
       - no bed-heating command (M140 / M141 / M190) anywhere
       - every move inside 0..100 on X, Y and Z, with NO tolerance
       - nozzle temperature inside the printer's 180-230 C
       - support material present if the part barely touches the bed
       - a filename of plain letters and digits, which is all the K10 reads
"""

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
PROFILES = os.path.join(HERE, "profiles")
MACHINE = os.path.join(PROFILES, "machine.json")
PROCESS = os.path.join(PROFILES, "process-factory.json")
FILAMENT = os.path.join(PROFILES, "filament-pla.json")

BED = (100.0, 100.0, 100.0)          # K10 manual, section 1.2
NOZZLE_RANGE = (180, 230)            # K10 manual, section 1.2
PRIME_LINE_Y = 3.5                   # the start G-code primes along Y 2..3.5
RAFT_MARGIN = 3.0                    # factory is 5 mm; 3 keeps a raft under wider parts
MIN_CONTACT = 0.5                    # below this share of footprint, supports are required

ORCA_CANDIDATES = [
    "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
    r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    r"C:\Program Files\OrcaSlicer\OrcaSlicer.exe",
    "/usr/bin/orca-slicer",
    "/usr/local/bin/orca-slicer",
]


# --------------------------------------------------------------------------- STL

def read_stl(path):
    """Triangles as [((x,y,z),(x,y,z),(x,y,z)), ...]. Binary or ASCII."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            tris = []
            for i in range(n):
                v = struct.unpack("<12f", data[84 + 50 * i: 84 + 50 * i + 48])
                tris.append((v[3:6], v[6:9], v[9:12]))
            return tris
    text = data.decode("ascii", errors="replace")
    verts = [tuple(float(c) for c in m) for m in
             re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)]
    if not verts or len(verts) % 3:
        raise ValueError(f"{path}: not a readable STL")
    return [tuple(verts[i:i + 3]) for i in range(0, len(verts), 3)]


def write_stl(path, tris):
    with open(path, "wb") as f:
        f.write(b"k10slice oriented copy".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            ux, uy, uz = (b[i] - a[i] for i in range(3))
            vx, vy, vz = (c[i] - a[i] for i in range(3))
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write(struct.pack("<12fH", nx / ln, ny / ln, nz / ln, *a, *b, *c, 0))


def rotate(tris, rx=0.0, ry=0.0):
    """Rotate about X then Y, in degrees. Proper rotations keep the winding valid."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))

    def r(p):
        x, y, z = p
        y, z = y * cx - z * sx, y * sx + z * cx
        x, z = x * cy + z * sy, -x * sy + z * cy
        return (x, y, z)
    return [(r(a), r(b), r(c)) for a, b, c in tris]


def extents(tris):
    pts = [p for t in tris for p in t]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo, hi


def bed_contact(tris):
    """(area in mm2 resting within 0.3 mm of the lowest point, share of footprint)."""
    lo, hi = extents(tris)
    touching = 0.0
    for a, b, c in tris:
        if max(a[2], b[2], c[2]) > lo[2] + 0.3:
            continue
        touching += abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2.0
    footprint = max((hi[0] - lo[0]) * (hi[1] - lo[1]), 1e-9)
    return touching, touching / footprint


ORIENTATIONS = [(0, 0), (180, 0), (90, 0), (270, 0), (0, 90), (0, 270)]


def best_orientation(tris):
    """The face-down orientation with the most area on the bed that also fits it."""
    best = None
    for rx, ry in ORIENTATIONS:
        t = rotate(tris, rx, ry)
        lo, hi = extents(t)
        size = [hi[i] - lo[i] for i in range(3)]
        if any(size[i] > BED[i] for i in range(3)):
            continue
        area, _ = bed_contact(t)
        if best is None or area > best[0] + 1.0:   # ties keep the earlier, i.e. as modelled
            best = (area, rx, ry)
    if best is None:
        return None
    return best[1], best[2]


# --------------------------------------------------------------------------- audit

def audit(path, low_contact):
    text = open(path).read()
    problems = []
    bed_cmds = sorted(set(re.findall(r"^(M140|M141|M190)\b", text, re.M)))
    if bed_cmds:
        problems.append(f"bed-heating commands {bed_cmds}: the K10 has no heated bed, "
                        f"and M190 would make it wait forever")
    for axis, limit in zip("XYZ", BED):
        vals = [float(v) for v in re.findall(rf"^G[01] [^;\n]*{axis}(-?\d+\.?\d*)", text, re.M)]
        if vals and (min(vals) < 0.0 or max(vals) > limit):
            problems.append(f"{axis} runs {min(vals):.2f} to {max(vals):.2f}, outside 0 to {limit:.0f}")
    temps = [int(t) for t in re.findall(r"^M10[49] S(\d+)", text, re.M)]
    hot = sorted({t for t in temps if t and not NOZZLE_RANGE[0] <= t <= NOZZLE_RANGE[1]})
    if hot:
        problems.append(f"nozzle temperatures {hot} outside {NOZZLE_RANGE[0]}-{NOZZLE_RANGE[1]} C")
    if low_contact and not re.search(r"^;TYPE:Support", text, re.M):
        problems.append("the part barely touches the bed and there is no support - "
                        "it would print into the air")
    if not re.fullmatch(r"[A-Za-z0-9]+\.gcode", os.path.basename(path)):
        problems.append("filename is not plain letters and digits")
    return problems


def summary(path):
    text = open(path).read()
    t = re.search(r"estimated printing time \(normal mode\) = (.+)", text)
    g = re.search(r"total filament used \[g\] = ([\d.]+)", text)
    return (t.group(1).strip() if t else "?"), (float(g.group(1)) if g else 0.0)


# --------------------------------------------------------------------------- slicing

def find_orca(explicit):
    for c in ([explicit] if explicit else []) + ORCA_CANDIDATES:
        if c and os.path.exists(c):
            return c
    found = shutil.which("orca-slicer") or shutil.which("OrcaSlicer")
    if found:
        return found
    sys.exit("OrcaSlicer not found. Install it from https://github.com/SoftFever/OrcaSlicer "
             "or pass --orca /path/to/OrcaSlicer")


def k10_name(stl_path, used):
    """Plain letters and digits, unique within this run."""
    stem = re.sub(r"[^A-Za-z0-9]", "", os.path.splitext(os.path.basename(stl_path))[0])[:20] or "part"
    name, i = stem, 2
    while name.lower() in used:
        name, i = f"{stem}{i}", i + 1
    used.add(name.lower())
    return name + ".gcode"


def slice_one(orca, stl, out_dir, args, used, tmp):
    tris = read_stl(stl)
    if args.rotate_x is not None or args.rotate_y is not None:
        rx, ry = args.rotate_x or 0.0, args.rotate_y or 0.0
    elif args.no_orient:
        rx, ry = 0.0, 0.0
    else:
        o = best_orientation(tris)
        if o is None:
            return None, [f"does not fit 100 x 100 x 100 in any orientation"]
        rx, ry = o
    t = rotate(tris, rx, ry)
    lo, hi = extents(t)
    w, d, h = (hi[i] - lo[i] for i in range(3))
    if w > BED[0] or d > BED[1] or h > BED[2]:
        return None, [f"{w:.1f} x {d:.1f} x {h:.1f} mm does not fit 100 x 100 x 100"]
    area, share = bed_contact(t)

    # raft margin: factory 3 mm, less if the bed (and the prime line) cannot take it
    room_x = (BED[0] - w) / 2.0 - 0.5
    room_y = (BED[1] - 2 * (PRIME_LINE_Y + 1.0) - d) / 2.0
    margin = max(0.0, min(RAFT_MARGIN, room_x, room_y))
    raft = not args.no_raft and margin >= 0.5
    notes = []
    if not args.no_raft and not raft:
        notes.append("too big for a raft on this bed - printing without one")

    process = json.load(open(PROCESS))
    process["raft_layers"] = "4" if raft else "0"
    process["raft_expansion"] = f"{margin:.2f}" if raft else "0"
    process["raft_first_layer_expansion"] = "0"
    if args.no_support:
        process["enable_support"] = "0"
    proc_path = os.path.join(tmp, "process.json")
    json.dump(process, open(proc_path, "w"), indent=2)

    oriented = os.path.join(tmp, os.path.basename(stl))
    write_stl(oriented, t)
    work = os.path.join(tmp, "out")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    # Rotation is done above in Python. OrcaSlicer 2.4.2 CRASHES on --rotate-x
    # combined with --ensure-on-bed, so no rotation flag ever reaches it.
    cmd = [orca, "--load-settings", f"{MACHINE};{proc_path}", "--load-filaments", FILAMENT,
           "--slice", "0", "--outputdir", work, oriented]
    res = subprocess.run(cmd, capture_output=True, text=True)
    produced = [f for f in os.listdir(work) if f.endswith(".gcode")]
    if not produced:
        tail = (res.stdout or "")[-300:] + (res.stderr or "")[-300:]
        return None, [f"OrcaSlicer produced nothing (exit {res.returncode}). {tail.strip()}"]

    target = os.path.join(out_dir, k10_name(stl, used))
    shutil.move(os.path.join(work, produced[0]), target)
    problems = audit(target, share < MIN_CONTACT)
    info = dict(rot=(rx, ry), size=(w, d, h), contact=share, raft=margin if raft else 0.0, notes=notes)
    if problems:
        os.remove(target)
        return None, problems
    return (target, info), []


def selftest(orca, out_dir):
    """Slice a 20 mm cube. Proves OrcaSlicer, the profiles and the audit all work."""
    tmp = tempfile.mkdtemp(prefix="k10-selftest-")
    s = 20.0
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    cube = os.path.join(tmp, "selftest_cube.stl")
    write_stl(cube, [(v[a], v[b], v[c]) for a, b, c in faces])
    return [cube]


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Slice STL files for the EasyThreed K10 with "
                                             "its factory settings, and audit the G-code.")
    ap.add_argument("stl", nargs="*", help="one or more .stl files")
    ap.add_argument("--out", default=None, help="output folder (default: next to each STL)")
    ap.add_argument("--rotate-x", type=float, default=None, help="force rotation about X, degrees")
    ap.add_argument("--rotate-y", type=float, default=None, help="force rotation about Y, degrees")
    ap.add_argument("--no-orient", action="store_true", help="slice exactly as modelled")
    ap.add_argument("--no-raft", action="store_true", help="skip the raft (faster, less grip)")
    ap.add_argument("--no-support", action="store_true", help="skip supports (flat parts only)")
    ap.add_argument("--orca", default=None, help="path to the OrcaSlicer executable")
    ap.add_argument("--selftest", action="store_true", help="slice a 20 mm test cube")
    args = ap.parse_args()

    orca = find_orca(args.orca)
    stls = list(args.stl)
    if args.selftest:
        stls += selftest(orca, args.out)
    if not stls:
        ap.error("give at least one .stl file, or --selftest")

    used, failed = set(), 0
    for stl in stls:
        if not os.path.exists(stl):
            print(f"FAIL {stl}: no such file")
            failed += 1
            continue
        out_dir = args.out or os.path.dirname(os.path.abspath(stl))
        if args.selftest and os.path.basename(stl) == "selftest_cube.stl" and not args.out:
            out_dir = os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="k10slice-")
        try:
            result, problems = slice_one(orca, stl, out_dir, args, used, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if problems:
            failed += 1
            print(f"FAIL {os.path.basename(stl)} - NOT written:")
            for p in problems:
                print(f"       ! {p}")
            continue
        path, info = result
        when, grams = summary(path)
        w, d, h = info["size"]
        rx, ry = info["rot"]
        raft = f"raft {info['raft']:.1f} mm" if info["raft"] else "no raft"
        print(f"OK   {os.path.basename(path):24} {when:>12}  {grams:5.1f} g   "
              f"{w:.0f}x{d:.0f}x{h:.0f} mm, rotated X{rx:+.0f} Y{ry:+.0f}, "
              f"{info['contact']:.0%} on bed, {raft}")
        for n in info["notes"]:
            print(f"       note: {n}")

    print()
    if failed:
        print(f"{failed} file(s) failed - nothing unsafe was written.")
        sys.exit(1)
    print("All files audited: no bed heating, inside 100 x 100 x 100, nozzle 180-230 C.")
    print("Put ONE file on the TF card at a time: the K10 prints the NEWEST file, not a chosen one.")


if __name__ == "__main__":
    main()
