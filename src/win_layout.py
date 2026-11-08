"""A full-width win capture that retains the original programme's artwork."""

CONTENT_SIZE = (1840, 220)
CANVAS_SIZE = (1920, 220)
SIDE_MARGIN = (CANVAS_SIZE[0]-CONTENT_SIZE[0])//2


def restore_win_geometry(saved, default, legacy_default, version):
    """Restore the reference aspect, preserving a custom width and bottom edge."""
    rounded = lambda geometry: [round(value) for value in geometry]
    if saved is None:
        return rounded(default)
    geometry = rounded(saved)
    if version >= 2:
        return geometry
    legacy = rounded(legacy_default)
    # Callers may construct the legacy tuple from the current banner height.
    # Recognize the actual old 1840x220 default from its shared bottom edge too.
    legacy_height = round(legacy[2]*220/1840)
    previous_default = [legacy[0], round(default[1]+default[3]-legacy_height),
                        legacy[2], legacy_height]
    if version < 1 and any(all(abs(a-b) <= 1 for a, b in zip(geometry, candidate))
                           for candidate in (legacy, previous_default)):
        return rounded(default)
    x, y, width, height = geometry
    new_height = max(30, round(width*CANVAS_SIZE[1]/CANVAS_SIZE[0]))
    return [x, y+height-new_height, width, new_height]
