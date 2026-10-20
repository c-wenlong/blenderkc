"""Materials, tuned against the reference shot and the original video's frames.

Each function returns a material, made once per scene and cached by name, so
call them freely. Pass a different `name` for a variant with other settings.
Colours are linear RGB (what Blender's Base Color takes).

    from blenderkc.kit import materials as M
    M.copper(), M.solder_mask(), M.dram_die(), M.epoxy(tint=(0.9, 0.7, 0.2))
"""
import bpy

from . import textures


def _get(name):
    m = bpy.data.materials.get(name)
    return m


def node_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    return m, nt, nt.nodes["Principled BSDF"]


def _set(b, **kw):
    names = {
        "color": "Base Color", "metallic": "Metallic", "rough": "Roughness", "coat": "Coat Weight",
        "coat_rough": "Coat Roughness", "coat_tint": "Coat Tint", "ior": "IOR", "transmission": "Transmission Weight",
        "aniso": "Anisotropic", "aniso_rot": "Anisotropic Rotation", "sheen": "Sheen Weight", "alpha": "Alpha",
        "emission": "Emission Color", "strength": "Emission Strength", "spec": "Specular IOR Level",
        "subsurface": "Subsurface Weight", "film": "Thin Film Thickness",
    }
    for k, v in kw.items():
        if v is None:
            continue
        sock = b.inputs[names[k]]
        if isinstance(v, (tuple, list)) and len(v) == 3 and sock.type == "RGBA":
            v = (*v, 1.0)
        sock.default_value = v


def principled(name, color=(0.5, 0.5, 0.5), metallic=0.0, rough=0.4, coat=0.0, **kw):
    """A plain Principled BSDF; extra keywords as in _set (transmission, ior, ...)."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    _set(b, color=color, metallic=metallic, rough=rough, coat=coat, **kw)
    return m


def socket(mat, name):
    """A Principled input of a material, to animate: shot.tween(socket(m, "Emission Strength"), "default_value", 5, a, b)."""
    return mat.node_tree.nodes["Principled BSDF"].inputs[name]


def _link(nt, a, b):
    nt.links.new(a, b)


def _n(nt, kind, **inputs):
    node = nt.nodes.new(kind)
    for k, v in inputs.items():
        if k.startswith("_"):
            setattr(node, k[1:], v)
        else:
            node.inputs[k].default_value = v
    return node


# --- studio ---------------------------------------------------------------

def floor_tiles(name="floor", scale=3.0, dark=0.010, light=0.015):
    """The reference's dark charcoal tiled floor, roughness varying by tile."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    brick = _n(nt, "ShaderNodeTexBrick", _offset=0.0, Scale=scale, **{"Mortar Size": 0.008, "Brick Width": 0.5,
               "Row Height": 0.5, "Color1": (dark, dark, dark * 1.1, 1), "Color2": (light, light, light * 1.13, 1),
               "Mortar": (dark / 2, dark / 2, dark * 0.6, 1)})
    _link(nt, tc.outputs["Object"], brick.inputs["Vector"])
    _link(nt, brick.outputs["Color"], b.inputs["Base Color"])
    ramp = _n(nt, "ShaderNodeMapRange", **{"From Min": 0.0, "From Max": 1.0, "To Min": 0.55, "To Max": 0.35})
    _link(nt, brick.outputs["Fac"], ramp.inputs["Value"])
    _link(nt, ramp.outputs["Result"], b.inputs["Roughness"])
    return m


def backdrop(name="backdrop", color=(0.42, 0.42, 0.43), rough=0.55, tiles=0.0):
    """The light grey seamless studio; tiles > 0 adds a faint small-tile grid."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    _set(b, color=color, rough=rough, spec=0.4)
    if tiles:
        tc = nt.nodes.new("ShaderNodeTexCoord")
        brick = _n(nt, "ShaderNodeTexBrick", _offset=0.0, Scale=tiles, **{"Mortar Size": 0.01, "Brick Width": 0.5,
                   "Row Height": 0.5, "Color1": (*color, 1), "Color2": (*(c * 0.93 for c in color), 1),
                   "Mortar": (*(c * 0.75 for c in color), 1)})
        _link(nt, tc.outputs["Object"], brick.inputs["Vector"])
        _link(nt, brick.outputs["Color"], b.inputs["Base Color"])
    return m


# --- metals ---------------------------------------------------------------

def copper(name="copper", rough=0.22):
    return principled(name, (0.93, 0.52, 0.36), metallic=1.0, rough=rough)


def brushed(name, color, rough=0.3, streak=0.12, scale=(1.0, 220.0, 1.0)):
    """A brushed metal: anisotropic, roughness streaked along local x."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    _set(b, color=color, metallic=1.0, aniso=0.7, aniso_rot=0.0)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = _n(nt, "ShaderNodeMapping", Scale=scale)
    noise = _n(nt, "ShaderNodeTexNoise", Scale=6.0, Detail=8.0)
    rr = _n(nt, "ShaderNodeMapRange", **{"To Min": rough - streak, "To Max": rough + streak})
    _link(nt, tc.outputs["Object"], mp.inputs["Vector"])
    _link(nt, mp.outputs["Vector"], noise.inputs["Vector"])
    _link(nt, noise.outputs["Fac"], rr.inputs["Value"])
    _link(nt, rr.outputs["Result"], b.inputs["Roughness"])
    return m


def brushed_copper(name="brushed_copper"):
    return brushed(name, (0.93, 0.55, 0.38), rough=0.28)


def gold(name="gold", rough=0.3):
    return principled(name, (1.0, 0.76, 0.33), metallic=1.0, rough=rough)


def solder(name="solder", rough=0.15):
    """Tin-silver solder and bright microbumps."""
    return principled(name, (0.85, 0.85, 0.88), metallic=1.0, rough=rough)


def aluminium(name="aluminium", rough=0.32):
    return principled(name, (0.91, 0.92, 0.93), metallic=1.0, rough=rough)


def steel(name="steel"):
    return brushed(name, (0.56, 0.57, 0.6), rough=0.32, streak=0.1)


def tantalum(name="tantalum"):
    """The Ta/TaN barrier: a dark, slightly blue metal."""
    return principled(name, (0.42, 0.44, 0.52), metallic=1.0, rough=0.28)


# --- boards and packages --------------------------------------------------

def solder_mask(name="solder_mask", color=(0.035, 0.11, 0.06), traces=True, seed=1, scale=1.0):
    """Glossy green solder mask (the reference package substrate), with copper
    routing showing faintly through it when traces is on."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    _set(b, color=color, rough=0.3, coat=0.8, coat_rough=0.08)
    img = textures.traces_image(seed=seed) if traces else None
    if img is not None:
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = _n(nt, "ShaderNodeMapping", Scale=(0.18 * scale, 0.18 * scale, 1.0))
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.image.colorspace_settings.name = "Non-Color"
        tex.extension = "REPEAT"
        _link(nt, tc.outputs["Object"], mp.inputs["Vector"])
        _link(nt, mp.outputs["Vector"], tex.inputs["Vector"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs["A"].default_value = (*color, 1)
        mix.inputs["B"].default_value = (*(min(1.0, c * 1.9 + 0.006) for c in color), 1)
        _link(nt, tex.outputs["Color"], mix.inputs["Factor"])
        _link(nt, mix.outputs["Result"], b.inputs["Base Color"])
        bump = _n(nt, "ShaderNodeBump", Strength=0.25, Distance=0.002)
        _link(nt, tex.outputs["Color"], bump.inputs["Height"])
        _link(nt, bump.outputs["Normal"], b.inputs["Normal"])
    return m


def board_black(name="board_black", seed=2):
    """A graphics card's near-black PCB."""
    return solder_mask(name, color=(0.010, 0.011, 0.012), seed=seed, scale=1.4)


def mould(name="mould", color=(0.018, 0.018, 0.02)):
    """Black epoxy mould compound: a memory chip's package top."""
    return principled(name, color, rough=0.55, spec=0.35)


def interposer(name="interposer"):
    return principled(name, (0.30, 0.30, 0.34), metallic=0.8, rough=0.18)


def gpu_lid(name="gpu_die"):
    """The GPU die as the reference drew it: satin silver."""
    return principled(name, (0.45, 0.46, 0.5), metallic=1.0, rough=0.4)


def logic_die(name="logic_die"):
    return principled(name, (0.12, 0.12, 0.14), metallic=0.4, rough=0.25, coat=0.5)


def ceramic(name="ceramic", color=(0.32, 0.25, 0.16)):
    """MLCC capacitor body."""
    return principled(name, color, rough=0.5)


def plastic(name="plastic", color=(0.02, 0.02, 0.02), rough=0.45):
    return principled(name, color, rough=rough)


# --- silicon --------------------------------------------------------------

def dram_die(name="dram_die", color=(0.42, 0.55, 0.06), cells=9.0):
    """The reference's yellow-green DRAM die with its cell grid."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    c2 = tuple(x * 0.86 for x in color)
    brick = _n(nt, "ShaderNodeTexBrick", _offset=0.0, Scale=cells, **{"Mortar Size": 0.02, "Brick Width": 0.6,
               "Row Height": 0.3, "Color1": (*color, 1), "Color2": (*c2, 1), "Mortar": (0.75, 0.78, 0.55, 1)})
    _link(nt, tc.outputs["Object"], brick.inputs["Vector"])
    _link(nt, brick.outputs["Color"], b.inputs["Base Color"])
    _set(b, metallic=0.35, rough=0.22, coat=0.6, coat_rough=0.05)
    return m


def wafer(name="wafer", color=(0.48, 0.48, 0.62), rough=0.1):
    """A polished silicon wafer surface: lavender grey and mirror-like."""
    return principled(name, color, metallic=0.55, rough=rough, coat=0.3)


def silicon_section(name="silicon_section", color=(0.13, 0.135, 0.2)):
    """Silicon seen in cross section: flat slate violet, satin."""
    return principled(name, color, metallic=0.0, rough=0.42, spec=0.45)


# --- translucent and process layers ---------------------------------------

def epoxy(name="epoxy", tint=(0.9, 0.72, 0.25), rough=0.12, density=0.3):
    """Translucent resin (underfill, MR-MUF mould, a substrate's epoxy)."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    _set(b, color=tint, rough=rough, transmission=1.0, ior=1.55)
    if density > 0:
        out = nt.nodes["Material Output"]
        vol = _n(nt, "ShaderNodeVolumeAbsorption", Color=(*tint, 1), Density=density)
        _link(nt, vol.outputs[0], out.inputs["Volume"])
    return m


def photoresist(name="photoresist", color=(0.30, 0.62, 0.55)):
    """Teal photoresist, slightly waxy."""
    return principled(name, color, rough=0.38, sheen=0.3, subsurface=0.15)


def oxide(name="oxide", color=(0.85, 0.92, 0.95)):
    """Silicon dioxide liner: clear and glassy."""
    return principled(name, color, rough=0.06, transmission=0.9, ior=1.46)


def glass_fibre(name="glass_fibre", color=(0.5, 0.51, 0.49), weave=25.0, resin=None):
    """Woven glass-fibre cloth; resin=(r, g, b) coats it in tinted epoxy (FR-4)."""
    m = _get(name)
    if m:
        return m
    m, nt, b = node_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = _n(nt, "ShaderNodeMapping", Scale=(weave, weave, weave))
    _link(nt, tc.outputs["Object"], mp.inputs["Vector"])
    # two families of bands (warp and weft) and a checker deciding which is on top
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    _link(nt, mp.outputs["Vector"], sep.inputs["Vector"])
    warp = _n(nt, "ShaderNodeTexWave", _wave_type="BANDS", _bands_direction="X", Scale=1.0, Distortion=0.6,
              Detail=2.0)
    weft = _n(nt, "ShaderNodeTexWave", _wave_type="BANDS", _bands_direction="Y", Scale=1.0, Distortion=0.6,
              Detail=2.0)
    _link(nt, mp.outputs["Vector"], warp.inputs["Vector"])
    _link(nt, mp.outputs["Vector"], weft.inputs["Vector"])
    chk = _n(nt, "ShaderNodeTexChecker", Scale=2.0)
    chk.inputs["Color1"].default_value = (1, 1, 1, 1)
    chk.inputs["Color2"].default_value = (0, 0, 0, 1)
    _link(nt, mp.outputs["Vector"], chk.inputs["Vector"])
    pick = nt.nodes.new("ShaderNodeMix")
    pick.data_type = "FLOAT"
    _link(nt, chk.outputs["Fac"], pick.inputs["Factor"])
    _link(nt, warp.outputs["Fac"], pick.inputs["A"])
    _link(nt, weft.outputs["Fac"], pick.inputs["B"])
    fibre = _n(nt, "ShaderNodeTexNoise", Scale=900.0, Detail=4.0)
    _link(nt, tc.outputs["Object"], fibre.inputs["Vector"])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    _link(nt, pick.outputs["Result"], mul.inputs[0])
    _link(nt, fibre.outputs["Fac"], mul.inputs[1])
    col = nt.nodes.new("ShaderNodeMix")
    col.data_type = "RGBA"
    col.inputs["A"].default_value = (*(c * 0.55 for c in color), 1)
    col.inputs["B"].default_value = (*color, 1)
    _link(nt, mul.outputs[0], col.inputs["Factor"])
    _link(nt, col.outputs["Result"], b.inputs["Base Color"])
    bump = _n(nt, "ShaderNodeBump", Strength=0.6, Distance=0.004)
    _link(nt, mul.outputs[0], bump.inputs["Height"])
    _link(nt, bump.outputs["Normal"], b.inputs["Normal"])
    _set(b, rough=0.55, sheen=0.4)
    if resin is not None:
        _set(b, coat=1.0, coat_rough=0.06, coat_tint=resin)
    return m


def resin(name="resin", color=(0.36, 0.4, 0.22)):
    """Opaque build-up dielectric (ABF) as seen on a cut face: satin resin."""
    return principled(name, color, rough=0.35, coat=0.4, coat_rough=0.1, subsurface=0.1)


# --- light ----------------------------------------------------------------

def emission(name="glow", color=(0.2, 1.0, 0.35), strength=6.0):
    """Pure emission (glowing text, beams, lit traces)."""
    m = _get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    em = _n(nt, "ShaderNodeEmission", Color=(*color, 1), Strength=strength)
    _link(nt, em.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return m


def tsv_copper(name="tsv_copper"):
    """Copper whose emission a shot can raise (TSVs lighting up)."""
    return principled(name, (0.95, 0.55, 0.30), metallic=1.0, rough=0.25, emission=(1.0, 0.45, 0.12), strength=0.0)


def emission_socket(mat):
    """The socket to tween for a material's glow (Principled or emission)."""
    nt = mat.node_tree
    if "Principled BSDF" in nt.nodes:
        return nt.nodes["Principled BSDF"].inputs["Emission Strength"]
    for n in nt.nodes:
        if n.type == "EMISSION":
            return n.inputs["Strength"]
    raise KeyError(f"material {mat.name} has no emission")
