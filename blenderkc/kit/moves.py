"""Camera moves as functions of the shot (thin wrappers over shot.camera, for
scripts that read better as moves.orbit(shot, ...)). Times are seconds.

    moves.dolly(shot, 0, 3, 2.0)           travel 2 units along the view
    moves.orbit(shot, 2, 6, 40)            swing 40 degrees round the look point
    moves.look_at(shot, 1, 2, part.point("top"))
    moves.push_in(shot, 4, 6, 0.3)         30% closer to the look point
    moves.rack_focus(shot, 5, 6, obj)      pull focus only
    moves.through(shot, [(0, pos, look), (2, pos, look), (5, pos, look)])
"""


def dolly(shot, start, end, distance, ease="inout"):
    return shot.camera.dolly(start, end, distance, ease)


def orbit(shot, start, end, degrees, center=None, rise=0.0, radius=None, ease="inout"):
    return shot.camera.orbit(start, end, degrees, center, rise, radius, ease)


def look_at(shot, start, end, point, ease="inout"):
    return shot.camera.look_at(start, end, point, ease)


def push_in(shot, start, end, amount=0.3, ease="inout"):
    return shot.camera.push_in(start, end, amount, ease)


def pull_out(shot, start, end, amount=0.3, ease="inout"):
    return shot.camera.pull_out(start, end, amount, ease)


def rack_focus(shot, start, end, to, ease="inout"):
    return shot.camera.rack_focus(start, end, to, ease)


def truck(shot, start, end, offset, ease="inout"):
    return shot.camera.truck(start, end, offset, ease)


def through(shot, keys):
    return shot.camera.through(keys)
