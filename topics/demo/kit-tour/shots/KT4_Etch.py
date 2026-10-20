"""A wafer cut through one TSV in the grey studio: SF6 plasma and ions etch
the hole in Bosch cycles, the walls are lined, and copper fills it."""
from blenderkc import kit
from blenderkc.kit import effects, parts


def build(shot):
    kit.rigs.grey_studio(shot, scale=0.4)
    wafer = parts.wafer_section("wafer", (0, 0, 1.8), size=(3.0, 1.2, 1.6), r=0.11, depth=1.1, scallops=7)

    l0, l1 = shot.line(0), shot.line(1)
    etch_end = l0.end + 0.2
    effects.plasma(shot, (0, 0.0, 2.3), (3.0, 1.2, 0.8), l0.start, etch_end)
    effects.ions(shot, (0, -0.55, 2.55), (0.18, 0.05), l0.start + 0.3, etch_end, count=30, fall=1.2, speed=1.6, r=0.007)
    wafer.etch(shot, l0.start + 0.3, etch_end)
    wafer.strip_resist(shot, etch_end, etch_end + 0.4)
    wafer.deposit(shot, "liner", shot.at("oxide", line=1), shot.at("oxide", line=1) + 0.4)
    wafer.deposit(shot, "barrier", shot.at("tantalum", line=1), shot.at("tantalum", line=1) + 0.4)
    wafer.deposit(shot, "seed", shot.at("seed", line=1), shot.at("seed", line=1) + 0.4)
    wafer.fill(shot, shot.at("fills", line=1) - 0.3, l1.end + 0.2)

    cam = shot.camera
    cam.set(pos=(0.6, -6.6, 2.6), look=(0, -0.6, 1.75), lens=50, fstop=5.6)
    cam.move(l1.start - 0.4, l1.start + 1.2, pos=(0.2, -4.4, 1.7), look=(0, -0.6, 1.3))

    shot.label("Photoresist", anchor=wafer.point("resist"), start=0.3, end=etch_end, side="right", dx=150, dy=-60)
    shot.equation("SF₆ + e⁻ → SF₅⁺ + F· + 2e⁻", start=l0.start + 0.6, end=etch_end, at=(960, 170), size=46)
    shot.label("Silicon", anchor=wafer.point("surface"), start=0.6, end=l1.start - 0.4, side="right", dx=150, dy=90)
    shot.label("Copper", anchor=wafer.point("hole0"), start=shot.at("fills", line=1), side="right", dx=180, dy=-40)
