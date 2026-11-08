"""Cumulative points ordered by the first time each total was reached."""
from __future__ import annotations

from event_penalties import penalty_totals


def team_identity(profile):
    return profile.get('team_id') or profile.get('team') or 'unassigned:' + profile['id']


def match_seats(match):
    """Use recorded seat order, including archives from before explicit seats."""
    if match.get('seats'):
        return match['seats']
    if match.get('initial_players'):
        return [player['id'] for player in match['initial_players']]
    if match.get('hands'):
        return [player['id'] for player in match['hands'][0]['players']]
    return [player['id'] for player in match.get('profiles', [])]


def _contributions(match, mode):
    seat_order = {identity: index for index, identity in enumerate(match_seats(match))}
    player_penalties, team_penalties = penalty_totals(match)
    changes = {}
    for result in match['results']:
        identity = team_identity(result) if mode == 'team' else result['id']
        seat = seat_order.get(result['id'], 4)
        points, earliest = changes.get(identity, (0., seat))
        # A team reaches its new total once per half, after every member's pt.
        net = result['points'] - player_penalties.get(result['id'], 0.)
        changes[identity] = (points + net, min(earliest, seat))
    if mode == 'team':
        for identity, penalty in team_penalties.items():
            if identity in changes:
                points, seat = changes[identity]
                changes[identity] = (points - penalty, seat)
    return changes


class ScoreTimeline:
    def __init__(self, rows):
        self.points = {row['id']: row['points'] for row in rows}
        self.first = {(identity, round(points, 6)): (0, 0)
                      for identity, points in self.points.items()}
        self.half = 0

    def record(self, match, mode='individual'):
        self.half += 1
        participants = []
        for identity, (delta, seat) in _contributions(match, mode).items():
            if identity not in self.points:
                continue
            self.points[identity] += delta
            self.first.setdefault((identity, round(self.points[identity], 6)), (self.half, seat))
            participants.append(identity)
        return participants

    def ranked(self, rows, *, projection=None, mode='individual', include_hidden=False):
        changes = _contributions(projection, mode) if projection else {}

        def points_at(row):
            identity = row['id']
            return self.points[identity] + changes.get(identity, (0., 4))[0]

        def order(row):
            identity = row['id']
            points = round(points_at(row), 6)
            # When neither chronology nor seats distinguish rows, Python's
            # stable sort retains their order in the roster.
            return (-points, self.first.get((identity, points),
                    (self.half + 1, changes.get(identity, (0., 4))[1])))

        ranked = []
        previous = None
        for rank, row in enumerate(sorted((row for row in rows if not row.get('hidden')), key=order), 1):
            points = points_at(row)
            ranked.append(dict(row, points=round(points, 4), rank=rank,
                               gap=round(previous - points, 4) if previous is not None else 0.))
            previous = points
        if include_hidden:
            ranked.extend(dict(row, points=round(points_at(row), 4), rank=None, gap=0.)
                          for row in rows if row.get('hidden'))
        return ranked
