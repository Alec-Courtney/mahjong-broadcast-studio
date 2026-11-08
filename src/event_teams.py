"""Real team identities and the team list used by league registration."""

import re

DEFAULT_COLOR = '#377e72'


def is_rgb_color(value):
    return isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) is not None


def assigned_team_id(profile):
    identity = profile.get('team_id') or profile.get('team') or ''
    if isinstance(identity, str) and identity.strip() and len(identity) <= 200 and not identity.startswith('unassigned:'):
        return identity
    return ''


def player_color(profile, teams):
    if profile.get('follow_team_color'):
        color = teams.get(assigned_team_id(profile), {}).get('color')
        if color:
            return color
    return profile.get('color', DEFAULT_COLOR)


def known_teams(state, event):
    """Read registered teams and recover the teams in older sessions."""
    broadcast = state['broadcast']
    registered = broadcast.get('teams', {})
    teams = {identity: dict(team) for identity, team in registered.items()}

    def include(profile):
        identity = assigned_team_id(profile)
        if not identity:
            return
        label = profile.get('team')
        name = label.strip()[:200] if isinstance(label, str) else ''
        team = teams.setdefault(identity, {'name': name or identity, 'team_logo': ''})
        color = profile.get('color')
        if 'color' not in team and is_rgb_color(color):
            team['color'] = color.lower()
        if identity in registered:
            return
        if name and team['name'] == identity:
            team['name'] = name
        logo = profile.get('team_logo')
        if not team['team_logo'] and isinstance(logo, str) and len(logo) <= 200:
            team['team_logo'] = logo

    for player in broadcast['roster']:
        include(player)
    for match in broadcast['matches']:
        if match.get('committed'):
            for player in match['results']:
                include(player)
    for identity, base in event.get('team_baselines', {}).items():
        include({'team_id': identity, 'team': base.get('name') or identity, 'color': base.get('color')})
    for team in teams.values():
        team.setdefault('color', DEFAULT_COLOR)
    return teams


def validate_teams(teams):
    if not isinstance(teams, dict) or len(teams) > 1000:
        raise ValueError('队伍列表格式无效')
    for identity, team in teams.items():
        if (not isinstance(identity, str) or not identity.strip() or len(identity) > 200
                or identity.startswith('unassigned:') or not isinstance(team, dict)):
            raise ValueError('队伍编号无效')
        name, logo = team.get('name'), team.get('team_logo', '')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('请填写有效的队伍名称')
        if not isinstance(logo, str) or len(logo) > 200:
            raise ValueError('队徽文件无效')
        if 'color' in team and not is_rgb_color(team['color']):
            raise ValueError('队伍颜色无效，请填写如 #377e72')


def assign_team(player, identity, teams):
    """Mirror the selected team's current identity into a player profile."""
    team = teams.get(identity)
    if identity and team is None:
        raise ValueError('所选队伍已变更，请重新选择')
    player.update(team_id=identity, team=team['name'] if team else '',
                  team_logo=team.get('team_logo', '') if team else '')
