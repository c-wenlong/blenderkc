"""Layers of a package substrate drop into place (glass fibre, epoxy, copper
foil, photoresist, UV through a mask), then a cut to the substrate in
cross section with its copper layers and laser vias."""
from blenderkc import kit
from blenderkc.kit import effects, materials as M, parts


def build(shot):
    kit.rigs.grey_studio(shot, scale=0.6, color=(0.3, 0.3, 0.31), tiles=1.2)
    stack = parts.layer_stack("build", [
        ("cloth", 0.08, M.glass_fibre()),
        ("epoxy", 0.05, M.epoxy("resin_coat", (0.8, 0.84, 0.55))),
        ("foil", 0.03, M.brushed_copper()),
        ("resist", 0.04, M.photoresist()),
    ], size=(3.0, 2.2), loc=(0, 0, 0.6))
    l0, l1 = shot.line(0), shot.line(1)
    stack.build_up(shot, shot.at("glass"), step=0.7, drop=0.8)
    effects.beams(shot, [(x * 0.5, y * 0.45) for x in range(-2, 3) for y in range(-2, 3)],
                  l0.end - 1.2, l1.slot_start, height=2.0, z=0.78)

    sect = parts.substrate_section("pkg", (20, 0, 0.6), size=(5.0, 1.6), copper_layers=4)

    cam = shot.camera
    cam.set(pos=(-2.6, -5.2, 3.6), look=(0, 0, 0.7), lens=50, fstop=4.0)
    cam.orbit(0, l1.slot_start, 25, rise=-0.6)
    cam.set(pos=(20.4, -6.0, 1.3), look=(20, 0, 0.85), at=l1.slot_start)
    cam.push_in(l1.slot_start, shot.duration, 0.25)

    shot.label("Glass fibre cloth", anchor=stack.point("cloth"), start=shot.at("glass") + 0.3, end=l1.slot_start)
    shot.label("Copper foil", anchor=stack.point("foil"), start=shot.at("copper") + 0.2, end=l1.slot_start)
    shot.label("Laser via", anchor=sect.point("via"), start=shot.at("vias", line=1))
    shot.label("Core", anchor=sect.point("core"), start=l1.start + 0.4, side="right")
