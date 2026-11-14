"""Persisted presentation flags belonging to an accepted table transition.

Renderers may animate a committed transition, but cannot be the only place
which decides the state a restarted application should restore.
"""
import copy


def settings_for_transition(settings, before, after, kind):
    candidate = copy.deepcopy(settings)
    if kind in ('ron', 'tsumo', 'draw', 'clear', 'next_hand', 'new_match', 'start_match', 'hand_correction'):
        candidate['final_points_mode'] = 'scores'
    if kind in ('ron', 'tsumo'):
        candidate.update(show_yaku=True, final_cards_visible=False)
    elif kind in ('clear', 'restore', 'next_hand', 'new_match', 'start_match', 'draw', 'hand_correction'):
        candidate.update(show_yaku=False, final_cards_visible=False)
    elif (before.get('broadcast', {}).get('config', {}).get('settlement_enabled', True)
          and not after.get('broadcast', {}).get('config', {}).get('settlement_enabled', True)):
        candidate['show_yaku'] = False
    return candidate
