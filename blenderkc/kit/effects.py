"""Effects: light and motion that are not parts.

    effects.signal_pulses(shot, bus, 1.0, 5.0, speed=3.0)   pulses run down wires
    effects.light_up(shot, bus, 2.0, 2.6)                    whole wires glow
    effects.glow_text("32 bit", (x, y, z), size=0.3)         lit 3D text on a board
    effects.glow(shot, material, 1.0, 2.0, 6.0)              raise any emission
    effects.plasma(shot, (0, 0, 1), (4, 2, 1.2), 1.0, 4.0)   an SF6 plasma volume
    effects.ions(shot, (0, 0, 1.5), (3, 1.5), 1.0, 4.0)      ions raining straight down
    effects.beams(shot, [(x, y)...], 1.0, 3.0)               UV light through a mask
"""
import math
import os
import random

import bpy

from . import geo
from . import materials as M

FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "Inter-SemiBold.ttf")


def _value(mat, key):
    return mat.node_tree.nodes[key].outputs[0]


def signal_pulses(shot, wires, start, end, speed=3.0, spacing=1.5, brightness=1.0, fade=0.3):
    """Pulses travel along every wire of a parts.wires bus from start to end
    (seconds) at `speed` units a second, one every `spacing` units."""
    m = wires.material
    shot.set(_value(m, "density"), "default_value", 1.0 / spacing, 0.0)
    travel = speed * (end - start) / spacing
    shot.tween(_value(m, "phase"), "default_value", travel, start, end, "linear", frm=0.0)
    shot.tween(_value(m, "pulse"), "default_value", brightness, start, start + fade, "linear")
    shot.tween(_value(m, "pulse"), "default_value", 0.0, end - fade, end, "linear")
    return wires


def light_up(shot, wires, start, end, level=0.35, ease="inout"):
    """The whole bus glows (level 0 is off)."""
    shot.tween(_value(wires.material, "lit"), "default_value", level, start, end, ease)
    return wires


def glow(shot, mat, start, end, strength, ease="inout"):
    """Tween a material's emission strength."""
    shot.tween(M.emission_socket(mat), "default_value", strength, start, end, ease)
    return mat


def glow_text(text, loc, size=0.3, color=(0.25, 1.0, 0.4), strength=8.0, rot=(0, 0, 0), name=None,
              parent=None, extrude=0.0, align="CENTER"):
    """Lit 3D text lying on a surface (rot in degrees). Returns the object;
    its material is <object>.active_material (animate with effects.glow)."""
    name = name or "text_" + "".join(c for c in text if c.isalnum())[:16]
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = text
    cu.font = bpy.data.fonts.load(FONT, check_existing=True)
    cu.size = size
    cu.align_x = align
    cu.align_y = "CENTER"
    cu.extrude = extrude
    o = bpy.data.objects.new(name, cu)
    o.location = loc
    o.rotation_euler = [math.radians(r) for r in rot]
    mat = M.emission(f"{name}_glow", color, strength)
    cu.materials.append(mat)
    return geo.link(o, parent)


def _volume_material(name, color, density, emission, scale, size):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    out = nt.nodes["Material Output"]
    # absorption plus emission, no scattering: a glowing gas at a fraction of the cost
    absorb = nt.nodes.new("ShaderNodeVolumeAbsorption")
    absorb.inputs["Color"].default_value = (*color, 1)
    glow = nt.nodes.new("ShaderNodeEmission")
    glow.inputs["Color"].default_value = (*color, 1)
    vol = nt.nodes.new("ShaderNodeAddShader")
    amount = nt.nodes.new("ShaderNodeValue")
    amount.name = amount.label = "amount"
    amount.outputs[0].default_value = 0.0
    w = nt.nodes.new("ShaderNodeValue")
    w.name = w.label = "w"
    tc = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.6
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(w.outputs[0], noise.inputs["W"])
    rr = nt.nodes.new("ShaderNodeMapRange")
    rr.inputs["From Min"].default_value = 0.3
    rr.inputs["From Max"].default_value = 0.62
    nt.links.new(noise.outputs["Fac"], rr.inputs["Value"])
    # fade toward the box's edges so the volume has no hard faces
    grad = nt.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "QUADRATIC_SPHERE"
    sep = nt.nodes.new("ShaderNodeMapping")
    sep.inputs["Scale"].default_value = (1.15 / size[0], 1.15 / size[1], 1.15 / size[2])
    nt.links.new(tc.outputs["Object"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Vector"], grad.inputs["Vector"])

    def mul(a, b):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = "MULTIPLY"
        nt.links.new(a, n.inputs[0])
        if isinstance(b, (int, float)):
            n.inputs[1].default_value = b
        else:
            nt.links.new(b, n.inputs[1])
        return n.outputs[0]

    shape = mul(rr.outputs["Result"], grad.outputs["Fac"])
    level = mul(shape, amount.outputs[0])
    nt.links.new(mul(level, density), absorb.inputs["Density"])
    nt.links.new(mul(level, emission), glow.inputs["Strength"])
    nt.links.new(absorb.outputs[0], vol.inputs[0])
    nt.links.new(glow.outputs[0], vol.inputs[1])
    nt.links.new(vol.outputs[0], out.inputs["Volume"])
    return m


def plasma(shot, loc, size, start, end, color=(0.62, 0.52, 0.02), density=12.0, emission=2.2, churn=0.6,
           scale=None, fade=0.6, parent=None):
    """A glowing gas volume (the SF6 etch plasma) filling a box of `size`
    centred at loc, fading in at start and out at end and churning between.
    Returns the object; pass end=None to leave it on."""
    s = min(size)
    c = bpy.context.scene.cycles
    c.volume_step_rate = max(c.volume_step_rate, 4.0)  # a soft glow needs no fine steps
    c.volume_max_steps = min(c.volume_max_steps, 256)
    o = geo.box("plasma", size, loc, None, 0, parent=parent)
    m = _volume_material("plasma_volume", color, density / s, emission, scale or 3.0 / s, size)
    o.data.materials.append(m)
    o.visible_shadow = False
    amount = m.node_tree.nodes["amount"].outputs[0]
    w = m.node_tree.nodes["w"].outputs[0]
    shot.tween(amount, "default_value", 1.0, start, start + fade, "inout", frm=0.0)
    if end is not None:
        shot.tween(amount, "default_value", 0.0, end - fade, end, "inout")
    shot.tween(w, "default_value", churn * shot.duration, 0.0, shot.duration, "linear", frm=0.0)
    return o


def ions(shot, loc, area, start, end, count=120, fall=1.6, speed=1.2, r=0.012, color=(0.6, 0.85, 1.0),
         strength=12.0, seed=11, parent=None):
    """Glowing ions falling straight down (directional bombardment) over an
    area (x, y) centred at loc, from loc's height down `fall` units, from
    start to end. They are instanced points moved every frame."""
    rnd = random.Random(seed)
    src = geo.source(f"_ion_{r:.3f}", lambda n: geo.sphere(n, r, mat=M.emission("ion_glow", color, strength),
                                                           segments=8, rings=4))
    seeds = [(rnd.uniform(-area[0] / 2, area[0] / 2), rnd.uniform(-area[1] / 2, area[1] / 2), rnd.random())
             for _ in range(count)]
    pts = [(x, y, 0.0) for x, y, _ in seeds]
    o = geo.instance_points("ions", src, pts, loc, parent)
    me = o.data
    far = (0.0, 0.0, 1000.0)

    def update(t):
        co = []
        for x, y, ph in seeds:
            if start <= t < end:
                z = -(((t - start) * speed / fall + ph) % 1.0) * fall
                co.extend((x, y, z))
            else:
                co.extend(far)
        me.vertices.foreach_set("co", co)
        me.update()

    shot.on_frame(update)
    return o


def beams(shot, points, start, end, height=3.0, r=0.012, color=(0.45, 0.25, 1.0), strength=3.0, z=0.0,
          parent=None):
    """Thin columns of light (UV exposure through a mask) at (x, y) points,
    from z up `height`, shown from start to end (fading up and down)."""
    k = 0
    while f"uv_beam{k}" in bpy.data.materials or f"_beam{k}" in bpy.data.objects:
        k += 1
    mat = M.emission(f"uv_beam{k}", color, 0.0)  # each group fades on its own
    src = geo.source(f"_beam{k}", lambda n: geo.bake(geo.cylinder(n, r, height, (0, 0, height / 2), mat, verts=10,
                                                                  cap=False)))
    o = geo.instance_points(f"beams{k}", src, [(x, y, z) for x, y in points], parent=parent)
    o.visible_shadow = False
    shot.show(o, start, end)
    sock = M.emission_socket(mat)
    shot.tween(sock, "default_value", strength, start, start + 0.3, "inout", frm=0.0)
    if end is not None:
        shot.tween(sock, "default_value", 0.0, end - 0.3, end, "inout")
    return o
