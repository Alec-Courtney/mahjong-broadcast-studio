"""Read-only, globally ranked sidebar pages and transient render proxies."""
from __future__ import annotations

import math
from types import SimpleNamespace

SIDEBAR_SIZE = (400, 650)
SIDEBAR_FIRST_Y = 86
SIDEBAR_ROW_HEIGHT = 52
SIDEBAR_BOTTOM_PADDING = 12


def _sidebar_presentation(data, participants):
    """Keep red rows for finding the table within a larger ranked field."""
    identities = {row.get('id') for row in data['all_rows']}
    count = len(data['all_rows'])
    compact = (data['pages'] == 1 and 0 < count <= 4
               and (data['scope'] == 'current' or identities == participants))
    height = SIDEBAR_FIRST_Y+count*SIDEBAR_ROW_HEIGHT+SIDEBAR_BOTTOM_PADDING if compact else SIDEBAR_SIZE[1]
    return dict(data, compact=compact, highlight_ids=frozenset() if compact else frozenset(participants),
                panel_size=(SIDEBAR_SIZE[0], height))


def is_team_event(model):
    event = getattr(model, 'event', None) or {}
    kind = event.get('kind')
    if kind in ('team_league', 'individual_league', 'individual_knockout'):
        return kind == 'team_league'
    return model.state.get('broadcast', {}).get('config', {}).get('event_mode') == 'team'


def sidebar_page_data(model, mode=None, page=None, rows=None, highlight_ids=None):
    """Choose a page without renumbering ranks or including unfinished pt.

    Current-table scope retains all four seats/teams and uses a short panel.
    Full fields keep fixed-height pagination, including partially filled pages.
    """
    from tournament import standings, table_standings

    config = model.state.get('broadcast', {}).get('config', {})
    mode = mode or config.get('sidebar_mode', 'individual')
    if mode not in ('team', 'individual'):
        mode = 'individual'
    all_rows = list(standings(model.state, mode=mode, live=False) if rows is None else rows)
    participants = ({row['id'] for row in table_standings(model.state, mode=mode, live=False)}
                    if highlight_ids is None else set(highlight_ids))
    scope = config.get('sidebar_scope', 'all')
    if scope == 'current':
        all_rows = [row for row in all_rows if row.get('id') in participants]
        return _sidebar_presentation(dict(rows=all_rows, all_rows=all_rows, page=0, pages=1,
                    page_size=4, scope='current'), participants)
    count = max(2, min(10, int(config.get('sidebar_page_size', 10))))
    pages = max(1, math.ceil(len(all_rows)/count))
    index = max(0, int(config.get('sidebar_page', 0) if page is None else page)) % pages
    return _sidebar_presentation(dict(rows=all_rows[index*count:(index+1)*count], all_rows=all_rows,
                page=index, pages=pages, page_size=count, scope='all'), participants)


def sidebar_panel_size(model, mode=None, page=None, rows=None, highlight_ids=None):
    return sidebar_page_data(model, mode, page, rows, highlight_ids)['panel_size']


def sidebar_render_model(model, page):
    """Override only a render view's page, preserving all authoritative objects."""
    state = dict(model.state)
    broadcast = dict(state.get('broadcast', {}))
    config = dict(broadcast.get('config', {}))
    config['sidebar_page'] = int(page)
    broadcast['config'] = config
    state['broadcast'] = broadcast
    return SimpleNamespace(state=state, settings=model.settings, path=model.path,
                           event=getattr(model, 'event', None))
