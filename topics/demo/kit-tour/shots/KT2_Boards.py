"""A graphics card with GDDR chips round the GPU, then a cut to a server
node whose GPU packages carry their memory inside."""
from blenderkc import kit
from blenderkc.kit import effects


def build(shot):
    kit.rigs.dark_studio(shot, scale=2.5)
    card = kit.parts.graphics_card()
    node = kit.parts.server_board(loc=(90, 0, 0))

    cam = shot.camera
    cam.set(pos=(-14, -20, 17), look=(-1.5, 0.5, 0), lens=45, fstop=4.0)
    cut = shot.line(1).slot_start
    cam.move(0, cut, pos=(-9, -17, 14), look=(-1.5, 0.6, 0))
    for i, chip in enumerate(card.chips):
        turn = -chip.root.rotation_euler.z * 57.29578  # keep the text upright
        effects.glow_text("32 bit", (0, 0, chip.height + 0.002), size=0.32, rot=(0, 0, turn), parent=chip.root,
                          name=f"bits{i}", strength=0.0)
        effects.glow(shot, bpy_mat(f"bits{i}"), shot.at("chips") + 0.05 * i, shot.at("chips") + 0.4 + 0.05 * i, 6.0)

    cam.set(pos=(64, -38, 30), look=(90, 0, 0), lens=45, fstop=5.6, at=cut)
    cam.move(cut, shot.duration, pos=(78, -27, 19), look=(90, 1, 0))
    shot.label("GPU", anchor=card.point("gpu"), start=0.6, end=cut - 0.3)
    shot.label("GDDR memory chip", anchor=card.point("gddr1"), start=shot.at("memory"), end=cut - 0.3,
               side="left")
    shot.label("HBM inside the package", anchor=node.point("gpu1"), start=shot.at("inside", line=1), dx=-120)


def bpy_mat(obj_name):
    import bpy

    return bpy.data.objects[obj_name].active_material
