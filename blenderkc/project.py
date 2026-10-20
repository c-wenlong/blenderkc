"""Camera-view coordinates to frame pixels. Pure Python.

bpy_extras.object_utils.world_to_camera_view gives (x, y, z): x and y are 0..1
across the camera frame from the bottom-left, z is the distance in front of the
camera (negative behind it). Overlays want pixels from the top-left of the
full-size (100%) frame.
"""

MARGIN = 0.02


def to_pixels(view, width, height, margin=MARGIN):
    """[x_px, y_px, visible] with visible 1 when the point is in front of the
    camera and inside the frame (plus a small margin), else 0."""
    x, y, z = view
    visible = 1 if (z > 0 and -margin <= x <= 1 + margin and -margin <= y <= 1 + margin) else 0
    return [round(x * width, 2), round((1.0 - y) * height, 2), visible]
