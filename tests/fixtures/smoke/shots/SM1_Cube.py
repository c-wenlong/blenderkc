"""A tiny shot for the smoke test: one box, a moving camera, one label."""
from blenderkc import kit


def build(shot):
    kit.rigs.dark_studio(shot)
    b = kit.geo.box("cube", (1, 1, 1), (0, 0, 0.5), kit.materials.copper())
    shot.camera.set(pos=(0, -8, 3), look=(0, 0, 0.5))
    shot.camera.orbit(0, shot.duration, 30)
    shot.tween(b, "location.z", 1.0, 0, shot.duration)
    shot.label("A cube", anchor=b, start=0.0)
