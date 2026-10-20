"""The reference shot rebuilt on the kit: a GPU package, then one HBM stack
lifts apart into its dies and its through-silicon vias light up."""
from blenderkc import kit


def build(shot):
    kit.rigs.dark_studio(shot)
    pkg = kit.parts.gpu_package(hero=2)
    hero = pkg.hero

    # the reference's frames 55 to 115 (plus two frames per layer): 1.8 s to 3.8 s
    hero.explode(shot, 1.8, 3.8, spread=0.16, lift=0.12, stagger=2 / 30)
    hero.glow(shot, 3.47, 4.47, strength=6.0)

    shot.camera.through([
        (0.0, (-7.5, -9.0, 6.5), (0.3, 0.0, 0.2)),
        (1.967, (-0.6, -6.2, 3.2), (1.2, -0.4, 0.35)),
        (4.967, (-0.6, -5.1, 2.7), (1.75, -0.68, 1.37)),
    ])

    shot.title("High bandwidth memory", start=0.2, end=1.6, sub="Eight dies in every stack")
    shot.label("Silicon interposer", anchor=pkg.point("interposer"), start=0.6, end=2.0)
    shot.label("Through-silicon vias", anchor=hero.layers[-1], start=3.6)
    shot.glow("8 dies", anchor=pkg.point("hero"), start=4.0, color="green", size=44, box=True)
