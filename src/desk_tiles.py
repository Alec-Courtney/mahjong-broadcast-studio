"""Tile sizes used only by the operator desk; 80% is the original size."""
TILE_SIZE_KEYS = ('console_meld_tile_scale', 'console_wait_tile_scale')
TILE_SIZE_RANGE = (40, 200)


def validate_tile_sizes(settings):
    for key in TILE_SIZE_KEYS:
        value = settings.get(key, 100)
        if type(value) is not int or not TILE_SIZE_RANGE[0] <= value <= TILE_SIZE_RANGE[1]:
            raise ValueError('导播台牌面尺寸须为40%到200%之间的整数')


def tile_scale(settings, region):
    return settings.get(f'console_{region}_tile_scale', 100) / 80.
