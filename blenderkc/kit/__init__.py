"""The 3D kit shot scripts build from.

    from blenderkc import kit
    kit.rigs.dark_studio(shot)            # floor, world light, area lights
    pkg = kit.parts.gpu_package(hero=2)   # parts are geo.Part objects
    kit.materials.copper()                # materials, cached by name
    kit.effects.signal_pulses(shot, wires, 1.0, 4.0)
    kit.moves.orbit(shot, 2, 6, 40)       # same as shot.camera.orbit
    kit.ease("inout")                     # an easing function
"""
from .. import ease as _ease
from . import effects, geo, materials, moves, parts, rigs, textures  # noqa: F401


def ease(name="inout"):
    return _ease.get(name)
