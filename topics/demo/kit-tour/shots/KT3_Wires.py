"""A memory chip wired to a processor: pulses run down the bus, the
bandwidth equation, then the whole bus lights up."""
from blenderkc import kit
from blenderkc.kit import effects, parts


def build(shot):
    kit.rigs.dark_studio(shot)
    chip = parts.bga_chip("gddr", (-2.6, 0, 0), size=(1.2, 1.4, 0.12))
    cpu = parts.bga_chip("processor", (2.4, 0, 0), size=(2.8, 2.8, 0.1), balls=(26, 26))
    lid = kit.geo.box("lid", (1.9, 1.9, 0.08), (0, 0, cpu.height + 0.04), kit.materials.gpu_lid(), 0.01,
                      parent=cpu.root)
    bus = parts.wires("bus", start=(-2.0, 0, 0.12), end=(1.0, 0, 0.12), n=8, spacing=0.1, r=0.016)

    cam = shot.camera
    cam.set(pos=(-1.4, -10.6, 6.4), look=(-0.1, 0, 0.0), lens=50, fstop=4.0)
    cam.move(0, shot.duration, pos=(-0.7, -9.0, 5.2), look=(0.0, 0, 0.0))

    t_rate = shot.at("clock")
    effects.signal_pulses(shot, bus, t_rate, shot.line(1).end, speed=2.0, spacing=0.8)
    effects.light_up(shot, bus, shot.at("wider", line=1), shot.at("wider", line=1) + 0.6, level=0.25)

    shot.equation("Bandwidth = [frequency] × [bus width]", start=t_rate, end=shot.line(1).start)
    shot.glow("8 × 32 bit", at=(960, 250), start=shot.at("wider", line=1), color="green", size=50, box=True)
    shot.label("Memory chip", anchor=chip.point("edge"), start=0.4, end=t_rate, side="left")
    shot.label("Processor", anchor=lid, start=0.6, end=t_rate)
