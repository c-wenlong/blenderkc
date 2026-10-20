# The shot library and the 3D kit

This is the bpy side of blenderkc: what a shot script can call. The CLI, the
pipeline and the renderer contract are in [DESIGN.md](DESIGN.md). Every
function below has a docstring with its full parameters.

## A shot script

`topics/<path>/shots/<Scene>.py` defines `build(shot)`. It builds the scene in
bpy and animates it on the narration clock. Everything is in seconds; no
script writes keyframes or frame numbers.

```python
from blenderkc import kit
from blenderkc.kit import effects, parts


def build(shot):
    kit.rigs.dark_studio(shot)
    pkg = parts.gpu_package(hero=2)
    t = shot.at("stack")                          # when line 0 says "stack"
    pkg.hero.explode(shot, t, t + 2)
    shot.camera.set(pos=(-7.5, -9, 6.5), look=(0.3, 0, 0.2))
    shot.camera.move(0, t, pos=(-0.6, -6.2, 3.2), look=pkg.point("hero"))
    shot.label("Silicon interposer", anchor=pkg.point("interposer"), start=t)
```

The reference shot rebuilt this way is
[topics/demo/kit-tour/shots/KT1_Package.py](../topics/demo/kit-tour/shots/KT1_Package.py).

## Shot

| Call | What it does |
| --- | --- |
| `shot.duration`, `shot.fps`, `shot.frames` | The scene's length (from narration, or the estimate) |
| `shot.frame(t)`, `shot.time(f)` | Seconds to 1-based frame and back (frame 1 is t = 0) |
| `shot.at(word, line=0, nth=1)` | When the nth (from 1) word or phrase of a line starts; raises naming the line if it is not said |
| `shot.after(word, line=0, nth=1)` | When it ends |
| `shot.line(i)` | `.start`/`.end` of the voice, `.slot_start`/`.slot_end`, `.text` |
| `shot.tween(target, "location.z", to, start, end, ease="inout")` | Animate any property: object transforms, a node socket's `default_value`, a light's `energy` |
| `shot.path(target, prop, fn, start, end)` | Follow `fn(u)` for eased u in 0..1 |
| `shot.set(target, prop, value, at)` | Step to a value |
| `shot.show(obj, start, end)`, `shot.hide(obj, at)` | Render visibility, children included |
| `shot.on_frame(fn)` | `fn(t)` every frame, for anything procedural |
| `shot.anchor(name, obj_or_point)` | A 3D point projected to pixels every frame |
| `shot.label(text, anchor, start, end, side)` | A label with a leader line that follows the anchor |
| `shot.title(text, start, end, sub, position)` | A title card (center, lower, top) |
| `shot.equation(text, start, end, at, anchor, accent)` | Plain Unicode maths; `[terms]` in the accent colour |
| `shot.glow(text, start, end, at, anchor, color, box)` | Lit text such as "1,152 GB" or "266" |

Eases: `linear`, `in`, `out`, `inout` (default), `smooth`, `expo`, `back`,
`sine`, `step`, or any `f(u)`.

Overlays are records only: they never touch bpy and are drawn on the frames
after rendering, so a wording fix re-composites without re-rendering 3D. Keep
every label, title and equation in sentence case. Ink is chosen from the
frame under the text (white on dark, near-black on bright, with a halo), so
no shot sets a text colour for its background. `at=(x, y)` is pixels on a
1920 x 1080 frame. A label whose text would leave the frame flips side.

## Camera (`shot.camera`, or `kit.moves`)

A camera tracking a look-at empty, with depth of field focused on a second
empty (defaults: 50 mm, f/2.2, the reference's opening framing).

| Move | |
| --- | --- |
| `set(pos, look, lens, fstop, focus, at=0)` | Jump (a cut) |
| `move(start, end, pos, look, lens, fstop, focus)` | Tween any of them; focus follows look |
| `through([(t, pos, look), ...])` | Glide through framings without stopping (the reference's move) |
| `orbit(start, end, degrees, center, rise, radius)` | Swing round the look point |
| `dolly(start, end, distance)` | Travel along the view, look point and all |
| `push_in(start, end, amount)`, `pull_out(...)` | Toward or away from the look point |
| `truck(start, end, offset)`, `look_at(start, end, point)`, `zoom(start, end, lens)` | |
| `rack_focus(start, end, to)` | Pull focus only |
| `follow(start, end, subject, blend)` | Keep looking at and focused on a part that moves |
| `drift(amount, period)` | A slow float on top of every move |
| `frame_box(lo, hi, direction)` | A (pos, look) pair that frames a box |

Points anywhere (`look`, `to`, `anchor`) can be a tuple, an object, a part, or
`part.point(name)`. A camera move reads the point once, when it is
scheduled; `follow()` tracks a moving subject every frame. Camera moves made
after a `follow()` (push_in, orbit) aim at where the followed subject's tweens
put it; motion made only by an `on_frame` callback is not seen there. Anchors for
overlays are always projected every frame, so labels stay on moving parts.

## The kit

Units follow the reference: a GPU package is 7 x 5 units. Parts are
`geo.Part` objects: `part.root` (move or tween it), `part["name"]` (a named
object), `part.point("name")` (an anchor that follows the part).

**Rigs** (`kit.rigs`)

- `dark_studio(shot, scale=1)`: the approved look. Dark charcoal tiled floor, the CC0 interior HDRI desaturated at 0.16, key, cool rim and warm fill.
- `grey_studio(shot, scale=1, color, tiles, rim_on_backdrop=False)`: a light grey seamless backdrop under soft lights, for cross sections and process steps. Its rim light sits just above the backdrop's curve, so it is light-linked off the backdrop by default (it left a large bright blob top right behind the subject in HB15 to HB21); `rim_on_backdrop=True` keeps the old look. `rigs.keep_off_backdrop(rig, key)` does the same for any of a rig's lights.
- `scale` sizes the lights for the subject (0.4 for a 3-unit wafer section, 2.5 for a 26-unit graphics card).

**Materials** (`kit.materials`, cached by name; pass `name=` for a variant)

- `floor_tiles`, `backdrop`: the two studios' floors.
- `copper`, `brushed_copper`, `gold`, `solder`, `aluminium`, `steel` (brushed), `tantalum`.
- `solder_mask(color, traces=True)`: glossy mask with copper routing showing through; `board_black` for a graphics card.
- `mould` (black EMC), `interposer`, `gpu_lid`, `logic_die`, `ceramic`, `plastic`.
- `dram_die`: the reference's yellow-green die with its cell grid.
- `wafer` (polished lavender silicon), `silicon_section` (silicon on a cut face).
- `epoxy(tint, density)` translucent resin (MR-MUF, underfill), `resin` (opaque build-up film), `photoresist`, `oxide`, `glass_fibre(weave, resin)` (woven cloth, FR-4 with resin).
- `emission`, `tsv_copper` (copper that can glow), `socket(mat, input)` and `emission_socket(mat)` to animate.

**Parts** (`kit.parts`)

- `gpu_package(stacks_per_side=2, hero, dies, mould)`: substrate, interposer, GPU die(s), HBM stacks; `pkg.stacks`, `pkg.hero`. The defaults rebuild the reference.
- `die_stack(n=8, detail)`: base die plus DRAM dies on microbumps with TSVs; `explode`, `collapse`, `glow`.
- `dram_die`, `bga_chip` (a GDDR chip with balls), `solder_balls`, `microbumps` (pillar plus solder cap, instanced).
- `graphics_card(gddr=12, layout="columns")`: board, GPU, GDDR chips round it, power stages, PCIe fingers, bracket; `card.chips`. `layout="ring"`: sixteen chips, four a side at a 1.45 pitch, `card.chips` clockwise from the top left (the RTX 5090 scenes).
- `server_board(n_gpus=4)`: GPU packages with HBM inside, connectors, power stages; `node.gpus`. Its 3.0-wide dies put the stacks at `stack_x=2.12`; `gpu_package` refuses a `stack_x` that would put a stack inside a die.
- `wires(start, end, n, spacing)`: a bus sharing one signal material.
- `layer_stack([(key, thickness, material), ...])`: slabs with `explode`, `collapse`, `build_up`.
- `substrate_section(copper_layers=4)`: an organic substrate cut open: core, copper traces, laser vias, plated through-hole, mask, balls.
- `wafer_section(holes, r, depth, scallops)`: silicon cut through a TSV; `etch` (Bosch scallops), `deposit("liner" | "barrier" | "seed")`, `fill`, `strip_resist`, `thin`.

**Bond wires** (`kit.bond_wires`, import it by name)

- `bond_wires(name, pairs, arc, r, droop)`: thin gold loops for `[(start, end), ...]`, one curve redrawn every frame from `bw.state` (`shown`, `sag`, `fall`, `arc`); `drive(shot, bw)` to animate, `socket(bw, "Base Color")` to recolour. HB29.

**Process room** (`kit.process`, import it by name)

- `no_backdrop_glint(rig, key="rim")`: light-links one of the rig's lights off the backdrop (`rigs.keep_off_backdrop`). `grey_studio` now does this for its rim light by default, so the scripts that still call it for the rim change nothing.
- `chamber(name, loc, r, h)`: a glass etch chamber cut open toward -y, steel base, copper RF coil (`ch.coil_mat`); `glow_coil(shot, ch, a, b, strength)`.
- `drift(shot, name, loc, area, start, end, rise, ...)`: drifting particles (fluorine radicals down, SiF4 up), instanced and moved every frame.
- `arc(name, R, r, loc, mat)`: a thin tube on a circular arc in the xz plane (a bowl's outline on a cut face).

**The TSV cross-section** (`from blenderkc.kit import tsv`; SET TSV, HB31 to HB41)

- `tsv.section(shot, name, loc, size, holes=, r_open=, core=, rho=, pitch=, grid=, opened=, resist=)` (keywords after `size`): a silicon block cut through its vias, rebuilt every frame from `sec.s` (`etch`, `open`, `liner`, `barrier`, `seed`, `fill`, `clip`, `visible`), so every stage is a value a shot tweens. The hole is a stack of overlapping scoops (`sec.s.etch = 2.5` is two scoops and half a third); the defaults give a hole 0.21 wide that reaches 10:1 at 28 scoops.
- Etching: `etch_to(n)`, `coat()` (polymer over the hole as it is), `punch()` (ions clear the floor's polymer, walls keep theirs), `bosch(cycles)`, `strip_polymer`, `open_resist`, `strip_resist`.
- Lining and filling: `deposit("liner" | "barrier" | "seed")` grow real shells with their cut edges on the face; `plate()` grows copper from the floor up behind a round meniscus.
- Back side: `thin(to)` grinds the block, `reveal(depth)` cuts every layer flat (the copper ends). `hide(start, end)` cuts away to another set.
- Points: `rim`, `wall`, `floor` (follows the etch), `copper`, `surface`, `resist`, `section`, `bottom`.
- Around it: `ion_rain` (streaks that land on the floor or the resist), `grinding_wheel`, `debris`, `transistors(sec)` (gates and wells beside the hole that can flash red), `plating_bath()` (glass tank, blue CuSO₄, anodes, a wafer on a clamp arm and `bath.dip`), `cheap_volumes()` (coarse volume steps for a plasma seen close up: 31 s to 6 s a still at 50%, no visible change).
- Two plasma boxes with identical bounds render only the first (Cycles skips a second volume entered at the same face): offset one when cross-fading gases.

**Dies and microbumps** (`from blenderkc.kit import bumps`; SET STACK, HB42)

- `bumps.tsv_die(name, loc, size, nx, ny, pitch, bumps=, shown=, body=)`: a thinned die with a grid of copper TSVs, cut through its front row in the SET TSV look, with copper pillar and solder cap microbumps on top (instanced, so a stack of dies is cheap). `grow_pillars`, `grow_caps`, `melt` (the caps slump by `die.slump`); `body=False` is the bumps alone; `bumps.bump_gap()` is the height a bump row adds.
- Points: `top`, `front`, `tsv`, `bump`, `cap`. Materials in `die.mats` (`tsv` copper that can glow, `bump`, `cap`).

**Annotations** (`from blenderkc.kit import annotate`)

- `annotate.dimension(shot, name, p0, p1, start, end, fade=)`: a glowing bar with arrowheads that draws itself from p0 to p1; points `start`, `middle`, `end` for its label.
- `annotate.tint(shot, name, lo, hi, y, start, end)`: a translucent coloured pane over part of a section face (the wafer that will be ground away).

**Stacking** (`kit.stacking`, sections 9 to 12 of the HBM video)

- `light_studio(shot, scale)`: `rigs.grey_studio` with its lights hidden from the camera, the rim light moved off the wall and a matte backdrop, so close-ups show no bright pool behind the subject.
- `silicon_stack(name, loc, n, size, t, gap, cols, rows, cuts)`: wide polished dies on copper-pillar microbumps with solder caps, seen side on as in the original's light-studio frames. `drop_in`, `reflow` (every cap flattens at once and the dies settle), `fill_gaps(mat, envelope=)` (resin creeps in from one side), `bend` (needs `cuts`), `add_thermal` (dummy bumps); points `top`, `front`, `side`, `die{i}`, `face{i}`, `gap{i}`, `thermal{i}`.
- `heat_material(name, z_mid, half)` with `heat_value` / `heat_spread`: dies whose emission is a heat map peaking at one height.
- `underfill`, `film`, `cure(shot, mat, a, b)`, `glow_copper`, `warm_steel`, `silicon`.
- `pedestal`, `press_pad`, `crack`, `mould_cavity` (a cut-away two-half mould), `bond_head`, `glow_ring`, `torus`.
- `wafer_disc` and `wafer_grid` (a patterned 300 mm wafer), `wafer_rack`, `dimm` (a memory module with blank chips).
- `trend_line` and `trend_grow` (a glowing line that draws itself), `animate_grid` (move an instanced grid's points every frame, e.g. bumps packing closer), `lit_interposer` (an interposer whose wiring can glow), `bonding_pair` (a microbump stack beside a hybrid bonded one).

**Effects** (`kit.effects`)

- `signal_pulses(shot, bus, start, end, speed, spacing)` and `light_up(shot, bus, ...)`.
- `glow(shot, material, start, end, strength)`, `glow_text(text, loc, size, color)`.
- `plasma(shot, loc, size, start, end)`: the SF6 plasma (absorbing, glowing gas).
- `ions(shot, loc, area, start, end)`: glowing ions falling straight down.
- `beams(shot, points, start, end)`: UV light columns through a mask (each call fades on its own).

**Wiring** (`from blenderkc.kit import wiring`)

- `wire(name, start, end, n, spacing)`: wires with their own `wire_material` (pulses, a whole-wire glow, and red crosstalk "noise"); the root sits at `start`, so tweening `root` "scale.x" from 0 draws them on, and each `wire.wires[i]` can slide in "location.y".
- `fan(name, start, end, n, w0, w1, strip)`: n flat strips in one mesh, a ribbon that spreads from `w0` to `w1` wide (1,024 hair-thin wires cost one object).
- `pulses(shot, mat, start, end, speed, spacing)` (calls chain, the phase carries on), `flicker(shot, mat, start, end, level)` (crosstalk).
- `trace(name, loc, length, bits, period, edge, amp, edges)`: the voltage drawn as a lit line above a wire, redrawn every frame from `trace.state` (`period`, `edge`, `phase`, `reveal`, `amp`), which shots tween like any property; levels between 0 and 1 work (PAM3). `drive(shot, tr)` once; `sample_marks(tr)` adds read marks; `flash(shot, tr.rise, color, ...)` lights the rising or falling edges.
- `sweep_material(name, look, glow, axis)` and `sweep(shot, mat, start, end, frm, to)`: metal that lights where a front has passed (balls, bumps); `ball_grid(name, blocks, pitch, r)` (BGA footprints in blocks, exact counts) and `bump_field(name, groups, pitch, down)` (microbump grids split into groups with their own materials).

**Geometry** (`kit.geo`): `box`, `cylinder` (or cone), `sphere(half=...)`,
`plane`, `mesh`, `empty`, `grid_array`, `instance_points` and `instance_grid`
(any number of copies for the cost of one), `halve_object`, `bake`, `Part`.

**Packaging** (`from blenderkc.kit import packaging as pk`; import it by name, it is not in `kit/__init__`):
how an ordinary chip is packaged, for HB15 to HB22. Animated looks are knobs, Value nodes in a material,
tweened with `pk.turn(shot, mat, "knob", to, start, end)` (or `pk.knob(mat, name)` for the socket).

- `process_studio(shot, scale, tile)`: `grey_studio` in the original's mid-dark grey with small floor tiles.
- `bare_die(bumps=)`: a DRAM die with a centre pad row; `crack()`, `glint()`, `light()`. `package_substrate(pad_points=)`.
- `fanout_section()`: substrate cut open with a flipped die; `light_layers`, `send_signals`, `pop_balls`, `cap`.
- `cloth_sheet`, `flow_resin` (knob flow), `press_platen` (knob lit), `laminate()`.
- `laminate_board()`: core, copper, photoresist on one circuit pattern (`circuit_image(seed)`); `roll_resist`,
  `expose`, `harden`, `develop`, `etch`, `strip`. `photomask()`, `light_column()`, `etch_tank()` (lets light
  through to what is in it), `patterned_layer()`, `laser(shot, points, ...)` (leaves vias).
- `conformal_slab(x0, x1, y0, y1, bottom, top)`: a slab following functions of x (a wire on a wavy surface);
  `UndercutLine(...).etch(shot, a, b, r)`: a line's section eaten in from both sides under its resist;
  `arrow()`: a small glowing flat arrow; `ghost()`, `glow_copper()`, `front_glow()`: fading and glowing materials.

**Interposer** (`from blenderkc.kit import interposer as ip`), for HB23 to HB26:

- `silicon_slab(trench=, fill=)`: polished lavender silicon with a line field; knobs trench, fill (a sweep),
  glow (a count, sweeping across). `trenches()`, `fill()`, `light_sweep()` (a camera-invisible light bar whose
  reflection glides over a mirror), `polished_look(rig)`, `no_horizon()`, `organic_piece(lines=)`.
- `cowos_package(lift=(interposer, chips))`: two GPU dies and moulded HBM stacks on a silicon interposer on an
  organic substrate, with fading materials; `mount`, `seat`, `ghost_chips` (see the dies inside the moulds),
  `signals`, `power`. `bridges(pkg)`: silicon bridges where each stack meets a die.

## Render cost

Measured on the M4 Pro (Metal), 1920 x 1080, 16 samples, steady state (the
first frame of a run adds about a second of scene sync):

| Demo shot | Seconds a frame |
| --- | --- |
| KT1_Package (the reference) | 3.9 to 4.4 |
| KT2_Boards (card, server node) | 4.4 |
| KT3_Wires (pulses) | 3.3 to 3.8 |
| KT4_Etch (plasma volume, booleans) | 10 to 10.5 |
| KT5_Substrate (layers, section) | 6.1 to 6.7 |

The plasma volume is the expensive thing; keep it on screen only while the
narration needs it (`plasma(..., end=...)`).

## Things that bit once

- `bpy==5.2.2`: geometry-nodes modifier inputs are read-only RNA, so
  instancing builds one node group per source object. Material and world
  `use_nodes` still works but warns.
- A box with no bevel is flat shaded; bevelled boxes shade smooth without
  hardened normals, which is the reference's soft, light-catching look.
- Instance sources ignore their own transform: bake offsets into the mesh
  (`geo.bake`).
- Cut faces of halved parts are shaded flat, or smooth shading bends them.
