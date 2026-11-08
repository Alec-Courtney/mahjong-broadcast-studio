"""Canonical operator hand order, independent of camera coordinates and Qt."""


def tile_sort_key(tile):
    if not isinstance(tile, str) or len(tile) != 2:
        return (4, 0, 0)
    number, suit = tile
    if suit in 'mps' and number in '0123456789':
        return ('mps'.index(suit), 5 if number == '0' else int(number), 0 if number == '0' else 1)
    if suit == 'z' and number in '1234567':
        return (3, int(number), 0)
    return (4, 0, 0)


def sort_hand_tiles(tiles):
    """Man/pin/sou/honors; red five beside normal five; unknown slots at end."""
    return sorted(tiles, key=tile_sort_key)
