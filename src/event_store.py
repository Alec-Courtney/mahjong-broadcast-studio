"""Local event library with shared operator preferences and isolated event state."""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QLockFile

from app_settings import default_settings
from event_metadata import KINDS, ID, now, new_event, validate_event
from session_repository import MAX_LOCAL_SESSION_BYTES, atomic_json, atomic_bytes, reject_constant, encode_session
from file_transaction import atomic_writes


DEFAULT_EVENT_START_POINTS = 25000
BUILTIN_DEMO_KEY = 'team_complete'
BUILTIN_DEMO_ID = uuid.uuid5(uuid.NAMESPACE_URL, 'MahjongOverlayStudio/builtin/team_complete').hex


def read_library_index(filename):
    """Validate the whole index before startup or a library transaction uses it."""
    filename = Path(filename)
    try:
        if filename.stat().st_size > 1_000_000:raise ValueError()
        index = json.loads(filename.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
        events = index.get('events') if isinstance(index, dict) else None
        if (not isinstance(index, dict) or type(index.get('version')) is not int or index['version'] != 1
            or not isinstance(events, list) or not events or len(events) > 10000
            or any(not isinstance(identity, str) or not ID.fullmatch(identity) for identity in events)
            or len(set(events)) != len(events) or index.get('active_id') not in events):
            raise ValueError()
        return index
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('赛事库索引无效，原文件已保留') from error


def resolve_active_directory(root):
    """A migrated library starts from its active session, never a legacy root copy."""
    root = Path(root).resolve()
    index_path = root / 'library.json'
    if not index_path.is_file():return root
    index = read_library_index(index_path)
    return root / 'events' / index['active_id']


def library_transaction(callback):
    @wraps(callback)
    def transaction(self, *args, **kwargs):
        with self._index_transaction():
            return callback(self, *args, **kwargs)
    return transaction


class EventStore:
    def __init__(self, model, root=None):
        self.model = model
        self.root = Path(root or model.path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'library.json'
        self._index_lock = None
        self._event_summaries = {}
        from application_preferences import ApplicationPreferences
        from media_store import register_asset_root
        register_asset_root(self.root,model.asset_root)
        previous_preferences = model.preferences
        previous_settings = copy.deepcopy(model.settings)
        try:
            if not model._explicit_preferences_root:
                model.preferences = ApplicationPreferences(self.root)
                model.preferences.apply(model.settings)
            with self._index_transaction(refresh=False):
                self._initialize()
        except Exception:
            model.preferences = previous_preferences
            model.settings = previous_settings
            raise
        model.event_store = self

    def _initialize(self):
        model = self.model
        if self.index_path.is_file():
            self.index = self._read_index()
            self._open(self.index['active_id'], save_current=False)
        else:
            name = model.state['broadcast']['config'].get('title') or model.state.get('title') or '我的赛事'
            kind = 'team_league' if model.state['broadcast']['config'].get('event_mode') == 'team' else 'individual_league'
            event = new_event(name[:100], kind)
            index = {'version': 1, 'active_id': event['id'], 'events': [event['id']]}
            target = self.directory(event['id'])
            if target.exists():raise ValueError('赛事目录已存在，未覆盖原文件')
            from media_store import manage_photos
            migrated = (copy.deepcopy(model.state) if (model.path/'session.json').is_file()
                        else model.fresh(start_points=DEFAULT_EVENT_START_POINTS))
            manage_photos(migrated, target, source_base=model.path)
            with model.preferences.transaction():
                preferences_baseline = copy.deepcopy(model.preferences.baseline)
                try:
                    with atomic_writes(self.root):
                        candidate_settings = copy.deepcopy(model.settings)
                        model.preferences.save(candidate_settings)
                        atomic_bytes(target/'session.json', encode_session(migrated,candidate_settings,model.last_info,event))
                        atomic_json(self.index_path, index)
                except Exception:
                    model.preferences.baseline = preferences_baseline
                    self._discard_unpublished(target)
                    raise
            model.repository.observe(target / 'session.json')
            self.index = index
            model.path = target; model.state = migrated; model.event = event
            model.settings = candidate_settings

    def _read_index(self):
        return read_library_index(self.index_path)

    @contextmanager
    def _index_transaction(self, *, refresh=True):
        if self._index_lock is not None:
            yield
            return
        lock = QLockFile(str(self.root / 'library.lock'))
        if not lock.tryLock(1000):raise ValueError('赛事库正在被其他窗口更新，请稍后重试')
        self._index_lock = lock
        try:
            if refresh:self.index = self._read_index()
            yield
        finally:
            self._index_lock = None
            lock.unlock()

    def _discard_unpublished(self, target):
        """Only remove the newly allocated event directory."""
        target = Path(target)
        if target.parent != self.root / 'events' or not ID.fullmatch(target.name) or target.resolve() != target:return
        try:
            shutil.rmtree(target)
        except OSError:
            pass

    def directory(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError('赛事编号无效')
        return self.root / 'events' / identity

    def _session_path(self, identity):
        path = self.directory(identity) / 'session.json'
        if path.resolve() != path:
            raise ValueError('赛事文件路径无效')
        return path

    def _library_payload(self, identity):
        path = self._session_path(identity)
        if path.stat().st_size > MAX_LOCAL_SESSION_BYTES:
            raise ValueError('本地赛事数据超过256MiB')
        payload = json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
        event = payload['event']
        if (event['id'] != identity or not isinstance(event['name'], str)
                or not event['name'].strip() or len(event['name']) > 100):
            raise ValueError('赛事文件中的编号或名称无效')
        return payload

    def list_events(self):
        from ranking_reports import confirmed_matches
        self.index = self._read_index()
        rows = []
        summaries = {}
        for identity in self.index['events']:
            try:
                stat = self._session_path(identity).stat()
                revision = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                cached = self._event_summaries.get(identity)
                if cached is not None and cached[0] == revision:
                    summary = cached[1]
                else:
                    payload = self._library_payload(identity)
                    event, state = payload['event'], payload['state']
                    summary = dict(event,players=len(state['broadcast']['roster']),
                        matches=len(confirmed_matches(state)),error='')
                summaries[identity] = (revision, summary)
                rows.append(dict(copy.deepcopy(summary), current=identity == self.model.event['id']))
            except (OSError, ValueError, KeyError, TypeError) as error:
                rows.append(dict(id=identity, name='无法读取的赛事', kind='', players=0, matches=0,
                                 current=False, updated_at='', error=str(error)))
        self._event_summaries = summaries
        return rows

    def read_history(self, identity):
        """Read the saved result snapshot without opening it or observing its revision."""
        if identity not in self._read_index()['events']:
            raise ValueError('赛事不在当前赛事库中')
        state, settings, _, _, event = self.model.repository.read(
            self._session_path(identity), expected_event_id=identity, observe=False, local=True)
        return state, event, bool(event.get('demo_suite') or settings.get('demo_active'))

    @library_transaction
    def ensure_builtin_demo(self):
        """Install the bundled sample once without opening or saving another event."""
        if self.index.get('builtin_demo_installed') is True:
            return
        target = self.directory(BUILTIN_DEMO_ID)
        self._session_path(BUILTIN_DEMO_ID)
        candidate = dict(self.index, builtin_demo_installed=True)
        if BUILTIN_DEMO_ID not in candidate['events']:
            candidate['events'] = [*candidate['events'], BUILTIN_DEMO_ID]
        if target.exists():
            # A completed demo snapshot may be left behind by an interrupted
            # install. Adopt it only if it identifies the bundled sample.
            _, _, _, _, event = self.model.repository.read(
                target/'session.json', expected_event_id=BUILTIN_DEMO_ID, observe=False, local=True)
            if event.get('demo_suite', {}).get('key') != BUILTIN_DEMO_KEY:
                raise ValueError('内置演示赛事目录已被占用')
            atomic_json(self.index_path, candidate)
        else:
            from demo_data import demo_league_event, load_demo_state
            from media_store import manage_photos
            from session_repository import validate_settings
            payload = load_demo_state(BUILTIN_DEMO_KEY)
            state = self.model.migrate(payload['state'], payload['version'])
            settings = dict(default_settings(), **payload['settings'])
            validate_settings(settings)
            event = demo_league_event(payload, BUILTIN_DEMO_ID)
            state['title'] = state['broadcast']['config']['title'] = event['name']
            validate_event(event, state)
            self.model.engine.validate(state)
            manage_photos(state, target, strict=True)
            try:
                with atomic_writes(self.root):
                    atomic_bytes(target/'session.json', encode_session(state, settings, payload.get('last_info'), event))
                    atomic_json(self.index_path, candidate)
            except Exception:
                self._discard_unpublished(target)
                raise
        self.index = candidate

    def _open(self, identity, save_current=True):
        model = self.model
        target = self.directory(identity)
        source = target / 'session.json'
        # Validate before saving or replacing the current in-memory match.
        state, settings, info, embedded, event = model.read_data(source, local=True)
        if event is None or event['id'] != identity:
            raise ValueError('赛事文件与赛事编号不一致')
        from media_store import manage_photos
        manage_photos(state, target, source_base=target, embedded=embedded, strict=True)
        # Replay reconstruction can validate another state and fail. Do it
        # before committing the index, using a candidate rather than the model.
        replay = SimpleNamespace(state=state, settings=settings, engine=model.engine, last_transition=None)
        if settings.get('local_demo_suite'):
            from demo_suite import restore_transition
            restore_transition(replay)
        if save_current:
            if model.settings.get('demo_active'):
                from demo_console import DemoSession
                DemoSession(model).restore()
            model.save()
        from application_preferences import capture_preferences
        preferences = capture_preferences(model.settings) if save_current else model.preferences.read()
        before = copy.deepcopy(model.state)
        candidate_settings = default_settings()
        candidate_settings.update(settings)
        if preferences is not None:candidate_settings.update(preferences)
        candidate_event = copy.deepcopy(event)
        candidate_index = copy.deepcopy(self.index)
        candidate_index['active_id'] = identity
        with atomic_writes(self.root):
            if not save_current:model.preferences.save(candidate_settings)
            if candidate_index != self.index:atomic_json(self.index_path, candidate_index)
        # Opening is a read of the target session. Publish only after the index
        # succeeds; do not rewrite that session merely to load its preferences.
        self.index = candidate_index
        model.path = target; model.state = state; model.settings = candidate_settings
        model.last_info = info; model.event = candidate_event
        model.undo = []; model.redo = []; model.last_transition = None
        if hasattr(model, '_demo_original_history'):del model._demo_original_history
        model.changed.emit(before, model.state, 'import')
        model.last_transition = replay.last_transition
        if model.settings.get('local_demo_suite') and settings.get('show_yaku') and info:
            model.settings['show_yaku']=True
        model.display_changed.emit()

    def _install_new(self, target, state, settings, info, event):
        """Commit a new complete snapshot and its index before publishing it."""
        model = self.model
        replay = SimpleNamespace(state=state, settings=settings, engine=model.engine, last_transition=None)
        if settings.get('local_demo_suite'):
            from demo_suite import restore_transition
            restore_transition(replay)
        if model.settings.get('demo_active'):
            from demo_console import DemoSession
            DemoSession(model).restore()
        model.save()
        from application_preferences import capture_preferences
        candidate_settings = default_settings()
        candidate_settings.update(settings)
        candidate_settings.update(capture_preferences(model.settings))
        candidate_index = copy.deepcopy(self.index)
        candidate_index['events'].append(event['id'])
        candidate_index['active_id'] = event['id']
        before = copy.deepcopy(model.state)
        try:
            with atomic_writes(self.root):
                atomic_bytes(target/'session.json', encode_session(state,candidate_settings,info,event))
                atomic_json(self.index_path, candidate_index)
        except Exception:
            self._discard_unpublished(target)
            raise
        model.repository.observe(target/'session.json')
        self.index = candidate_index
        model.path = target; model.state = state; model.settings = candidate_settings
        model.last_info = info; model.event = copy.deepcopy(event)
        model.undo = []; model.redo = []; model.last_transition = None
        if hasattr(model, '_demo_original_history'):del model._demo_original_history
        model.changed.emit(before, model.state, 'import')
        model.last_transition = replay.last_transition
        if candidate_settings.get('local_demo_suite') and settings.get('show_yaku') and info:model.settings['show_yaku'] = True
        model.display_changed.emit()

    @library_transaction
    def open(self, identity):
        if identity not in self.index['events']:
            raise ValueError('赛事不在当前赛事库中')
        current = self.model.event['id']
        if identity != current:
            self._open(identity, save_current=current in self.index['events'])

    @library_transaction
    def delete(self, identity, confirmation):
        if identity not in self.index['events']:
            raise ValueError('赛事不在当前赛事库中')
        remaining = [value for value in self.index['events'] if value != identity]
        if not remaining:
            raise ValueError('请先新建或导入另一场赛事，再删除最后一场赛事')

        model = self.model
        # A Model save holds this lock through both the session and preference
        # commit. Keep it while checking the name and removing the old path.
        with model.preferences.transaction():
            if confirmation != self._library_payload(identity)['event']['name']:
                raise ValueError('赛事名称不一致。请刷新赛事库后重新确认。')
            target = self.directory(identity)
            staged = target.with_name(f'.deleting-{identity}')
            if (target.resolve() != target or staged.resolve() != staged
                    or staged.exists() or staged.is_symlink()):
                raise ValueError('赛事目录路径异常或上次删除尚未清理，请检查赛事目录')

            active = self.index['active_id']
            current = model.event['id']
            if current == identity:
                self._open(active if active != identity else remaining[-1], save_current=False)
            elif active == identity:
                replacement = current if current in remaining else remaining[-1]
                candidate = dict(self.index, active_id=replacement)
                atomic_json(self.index_path, candidate)
                self.index = candidate

            candidate = dict(self.index, events=remaining)
            target.rename(staged)
            try:
                atomic_json(self.index_path, candidate)
            except BaseException:
                staged.rename(target)
                raise
            self.index = candidate
            try:
                if staged.resolve() != staged:
                    raise OSError('待清理目录已被移动')
                shutil.rmtree(staged)
            except OSError as error:
                raise OSError(f'赛事已从库中移除，但以下目录未清理完毕：\n{staged}') from error

    @library_transaction
    def create(self, name, kind, copy_config=False, *, settings_source=None, settings_file=None):
        from media_store import manage_photos
        model = self.model
        event = new_event(name, kind)
        if sum(bool(value) for value in (copy_config, settings_source, settings_file)) > 1:
            raise ValueError('请只选择一个赛事设置来源')
        template, embedded, source_base = None, None, model.path
        if settings_file:
            from settings_transfer import read_event_settings
            template, embedded = read_event_settings(model, settings_file)
            source_base = Path(settings_file).parent
        elif settings_source:
            from settings_transfer import event_template
            if settings_source not in self.index['events']:
                raise ValueError('所选赛事不在当前赛事库中')
            if settings_source == model.event['id']:
                template = event_template(model.state, model.settings, model.event)
            else:
                source_base = self.directory(settings_source)
                source_state, source_settings, _, embedded, source_event = model.repository.read(
                    self._session_path(settings_source), expected_event_id=settings_source, observe=False, local=True)
                template = event_template(source_state, source_settings, source_event)
        points = DEFAULT_EVENT_START_POINTS
        if template and 'start_points' in template['broadcast']:
            from tournament import _integer
            points = _integer(template['broadcast']['start_points'], '新半庄起点', 100, 1000000000)
        state = model.fresh(start_points=points); settings = default_settings()
        from application_preferences import capture_preferences
        settings.update(capture_preferences(model.settings))
        if copy_config:
            state['rules'] = copy.deepcopy(model.state['rules'])
            state['rules']['startPoints'] = DEFAULT_EVENT_START_POINTS
            state['broadcast']['config'] = copy.deepcopy(model.state['broadcast']['config'])
            state['broadcast']['config']['start_points'] = DEFAULT_EVENT_START_POINTS
            state['broadcast']['config'].pop('team_baselines',None)
            if kind == 'team_league' and 'teams' in model.state['broadcast']:
                state['broadcast']['teams'] = copy.deepcopy(model.state['broadcast']['teams'])
            roster = copy.deepcopy(model.state['broadcast']['roster'])
            for player in roster:
                player.update(base_points=0, games=0, stats={}, status='active')
                player.pop('result_baseline', None)
                player.pop('baseline_stats', None)
                player.pop('hidden', None)
                if kind != 'team_league':
                    player.pop('follow_team_color', None)
            state['broadcast']['roster'] = roster
            # ensure_broadcast must see the copied IDs, otherwise it inserts the
            # fresh state's four placeholder players into a real event roster.
            for player,source in zip(state['players'],model.state['players']):
                player.update(id=source['id'],name=source['name'])
            import tournament
            tournament.configure(state, {'seats': [p['id'] for p in model.state['players']]})
            settings = copy.deepcopy(model.settings)
            for key in ('demo_active', 'demo_id'):settings.pop(key, None)
            for key in ('planned_games', 'qualification_count', 'player_plans', 'team_plans', 'team_names', 'report_columns','report_headers'):
                event[key] = copy.deepcopy(model.event.get(key, event.get(key,{})))
        elif kind == 'team_league':
            for player in state['broadcast']['roster']:
                player['follow_team_color'] = True
        if template:
            from settings_transfer import apply_event_template
            apply_event_template(state, settings, event, template, new=True)
        settings['show_yaku'] = False; settings['final_points_mode'] = 'scores';settings['final_cards_visible'] = False
        if copy_config:
            config = state['broadcast']['config']
            config.update(qualification_calculation='auto', manual_conditions={},
                          qualification_final=False, qualification_focus='')
            if kind != 'team_league':
                for key in ('standings_mode', 'sidebar_mode'):
                    if config.get(key) == 'team':config[key] = 'individual'
        if copy_config and model.event['kind'] != kind:
            event['qualification_count'] = 0
        state['title'] = name.strip(); state['broadcast']['config'].update(
            title=name.strip(), event_mode='team' if kind == 'team_league' else 'individual',
            qualification_mode='team' if kind == 'team_league' else 'individual',
            qualification_slots=event.get('qualification_count') or 2)
        validate_event(event, state); model.engine.validate(state)
        target = self.directory(event['id'])
        if target.exists():raise ValueError('赛事目录已存在，未覆盖原文件')
        manage_photos(state, target, source_base=source_base, embedded=embedded, strict=True)
        self._install_new(target,state,settings,None,event)
        return event['id']

    @library_transaction
    def import_new(self, filename):
        model = self.model
        state, settings, info, embedded, imported = model.read_data(filename)
        event = copy.deepcopy(imported) if imported else new_event(
            state['broadcast']['config'].get('title') or state.get('title') or '导入赛事',
            'team_league' if state['broadcast']['config'].get('event_mode') == 'team' else 'individual_league')
        event.update(id=uuid.uuid4().hex, created_at=now(), updated_at=now())
        validate_event(event, state)
        from media_store import manage_photos,archive_import_file
        target = self.directory(event['id'])
        if target.exists():raise ValueError('赛事目录已存在，未覆盖原文件')
        manage_photos(state, target, source_base=Path(filename).parent, embedded=embedded, strict=True)
        try:
            archive_import_file(target, filename, event_owned=True)
            self._install_new(target,state,settings,info,event)
        except Exception:
            self._discard_unpublished(target)
            raise
        return event['id']

    def update(self, **patch):
        if 'id' in patch and patch['id'] != self.model.event['id']:
            raise ValueError('赛事编号创建后不可修改')
        if 'kind' in patch and patch['kind'] != self.model.event['kind']:
            raise ValueError('比赛类型在创建后不可修改，请新建赛事并选择所需类型')
        candidate = copy.deepcopy(self.model.event)
        candidate.update(patch); candidate['updated_at'] = now()
        validate_event(candidate, self.model.state)
        config_patch={}
        if 'team_baselines' in patch:config_patch['team_baselines']=candidate['team_baselines']
        if 'kind' in patch:config_patch['event_mode']='team' if candidate['kind']=='team_league' else 'individual'
        if 'qualification_count' in patch and candidate['kind'] != 'individual_knockout':
            config_patch['qualification_mode']='team' if candidate['kind']=='team_league' else 'individual'
            if candidate['qualification_count'] > 0:config_patch['qualification_slots']=candidate['qualification_count']
        before = copy.deepcopy(self.model.state)
        state = copy.deepcopy(before)
        if config_patch:
            import tournament
            state['broadcast']['config'].update(copy.deepcopy(config_patch))
            tournament.validate_broadcast(state)
            self.model.engine.validate(state)
        # Metadata and its broadcast mirror must be durable together. In
        # particular, a failed save must not publish a new cutoff or baseline.
        self.model._save_snapshot(state,self.model.settings,self.model.last_info,candidate)
        self.model.state = state;self.model.event = candidate
        if 'bracket' in patch or config_patch:
            self.model.undo.clear();self.model.redo.clear()
        if config_patch:self.model.changed.emit(before,state,'broadcast')
        self.model.display_changed.emit()

