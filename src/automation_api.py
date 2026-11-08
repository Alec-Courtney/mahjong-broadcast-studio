"""Versioned, JSON-only draft inbox. No Qt, detector, calculator or model dependency."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA_VERSION = 1
TILES = {f'{n}{s}' for s in 'mps' for n in range(10)} | {f'{n}z' for n in range(1, 8)}
FU_VALUES = {20, 25, *range(30, 141, 10)}


class DraftError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise DraftError(f'{label}无效')
    return value


def _tiles(values, label='牌面', *, unique=False):
    if not isinstance(values, list) or len(values) > 34 or any(not isinstance(t, str) or t not in TILES for t in values):
        raise DraftError(f'{label}格式无效')
    result = [('5' + t[1]) if unique and t.startswith('0') else t for t in values]
    if unique and len(set(result)) != len(result):
        raise DraftError(f'{label}不能重复')
    return list(result)


def validate_payload(payload):
    """Validate transport data only. Host revalidates game semantics on confirmation."""
    if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload.get('schema_version') != SCHEMA_VERSION:
        raise DraftError('请使用 schema_version: 1')
    kind = payload.get('kind')
    if kind not in ('tiles', 'waits', 'win'):
        raise DraftError('草稿类型须为 tiles、waits 或 win')
    player = _integer(payload.get('player'), 0, 3, '选手座位')
    context_id = payload.get('context_id')
    if not isinstance(context_id, str) or not context_id or len(context_id) > 128:
        raise DraftError('请先读取当前 context_id')
    source = payload.get('source', 'external')
    request_id = payload.get('request_id')
    if not isinstance(source, str) or not source.strip() or len(source) > 120:
        raise DraftError('来源名称无效')
    if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
        raise DraftError('request_id 无效')
    data = copy.deepcopy(payload.get('data'))
    if not isinstance(data, dict):
        raise DraftError('data 须为 JSON 对象')
    if kind == 'tiles':
        data['tiles'] = _tiles(data.get('tiles'))
        if not 1 <= len(data['tiles']) <= 14:
            raise DraftError('手牌须为 1 到 14 张；只提交暗手牌，副露从系统读取')
    elif kind == 'waits':
        data['waits'] = _tiles(data.get('waits'), '待牌', unique=True)
        if not data['waits']:
            raise DraftError('听牌草稿至少需要一张待牌')
        data['no_yaku'] = _tiles(data.get('no_yaku', []), '无役待牌', unique=True)
        if not set(data['no_yaku']) <= set(data['waits']):
            raise DraftError('无役待牌必须在待牌中')
        if type(data.get('furiten', False)) is not bool:
            raise DraftError('振听设置须为布尔值')
        if data.get('remaining') is not None:
            _integer(data['remaining'], 0, 136, '山存张数')
    else:
        if data.get('type') not in ('ron', 'tsumo'):
            raise DraftError('和牌方式须为 ron 或 tsumo')
        loser = _integer(data.get('loser', (player + 1) % 4), 0, 3, '放铳座位')
        if data['type'] == 'ron' and loser == player:
            raise DraftError('和牌和放铳不能为同一人')
        data['loser'] = loser
        _integer(data.get('han'), 0, 100, '番数')
        _integer(data.get('yakuman', 0), 0, 20, '役满倍数')
        if type(data.get('fu')) is not int or data.get('fu') not in FU_VALUES:
            raise DraftError('符数无效')
        if not data.get('yakuman') and data['han'] < 1:
            raise DraftError('和牌至少需要一番役')
        names = data.get('yaku')
        if not isinstance(names, list) or len(names) > 50 or not names or any(not isinstance(n, str) or not 1 <= len(n) <= 60 for n in names):
            raise DraftError('役种列表无效')
        if len(set(names)) != len(names):
            raise DraftError('役种不能重复')
        bonuses = data.setdefault('bonuses', {})
        if not isinstance(bonuses, dict) or not set(bonuses) <= {'dora', 'aka', 'ura'}:
            raise DraftError('宝牌数量格式无效')
        for key in ('dora', 'aka', 'ura'):
            _integer(bonuses.setdefault(key, 0), 0, 40, '宝牌数量')
    return dict(schema_version=SCHEMA_VERSION, kind=kind, player=player,
                context_id=context_id, source=source.strip(), request_id=request_id, data=data)


class DraftInbox:
    """Thread-safe boundary shared by in-process adapters and the optional HTTP host."""
    def __init__(self, max_pending=100):
        self._lock = threading.RLock()
        self._context = {}
        self._drafts = OrderedDict()
        self._requests = OrderedDict()
        self.max_pending = max_pending
        self.on_submitted = None

    def set_context(self, context):
        with self._lock:
            self._context = copy.deepcopy(context)
            for draft in self._drafts.values():
                if draft['status'] == 'pending' and draft['context_id'] != context['context_id']:
                    draft['status'] = 'stale'

    def context(self):
        with self._lock:
            return copy.deepcopy(self._context)

    def submit(self, payload):
        value = validate_payload(payload)
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            key = (value['source'], value['request_id']) if value['request_id'] else None
            if key and key in self._requests:
                previous_digest, identity = self._requests[key]
                if digest != previous_digest:
                    raise DraftError('同一 request_id 不能提交不同内容', 409)
                return copy.deepcopy(self._drafts[identity])
            if value['context_id'] != self._context.get('context_id'):
                raise DraftError('对局已改变，请重新读取 context 并生成草稿', 409)
            if self._context.get('state', {}).get('phase') != 'playing':
                raise DraftError('当前对局已结算，请开始下一局', 409)
            if sum(d['status'] == 'pending' for d in self._drafts.values()) >= self.max_pending:
                raise DraftError('待确认草稿已满，请先处理', 429)
            value.update(id=uuid.uuid4().hex, status='pending', created_at=time.time())
            self._drafts[value['id']] = value
            if key:
                self._requests[key] = (digest, value['id'])
            # Bound history as well as pending drafts; evict only finished entries.
            while len(self._drafts) > self.max_pending * 3:
                old = next((i for i, d in self._drafts.items() if d['status'] != 'pending'), None)
                if old is None:
                    break
                self._drafts.pop(old)
                for k, (_, identity) in list(self._requests.items()):
                    if identity == old:
                        self._requests.pop(k)
            result = copy.deepcopy(value)
        if self.on_submitted:
            self.on_submitted(result['id'])
        return result

    def list(self):
        with self._lock:
            return copy.deepcopy(list(self._drafts.values()))

    def get(self, identity, *, pending=False):
        with self._lock:
            if identity not in self._drafts:
                raise DraftError('未找到草稿', 404)
            value = self._drafts[identity]
            if pending and (value['status'] != 'pending' or value['context_id'] != self._context.get('context_id')):
                raise DraftError('草稿已处理或对局已改变，请重新生成', 409)
            return copy.deepcopy(value)

    def mark(self, identity, status):
        if status not in ('applied', 'dismissed'):
            raise DraftError('草稿状态无效')
        with self._lock:
            if identity not in self._drafts:
                raise DraftError('未找到草稿', 404)
            self._drafts[identity]['status'] = status


class LocalDraftServer:
    """Opt-in loopback transport. It can enqueue drafts; it cannot settle a hand."""
    def __init__(self, inbox):
        self.inbox = inbox
        self.token = secrets.token_urlsafe(32)
        self._server = None
        self._thread = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self._server.server_port}' if self._server else ''

    def start(self, port=0):
        if self._server:
            return self.url
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, value):
                body = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                expected = 'Bearer ' + owner.token
                if not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), expected.encode('utf-8')):
                    self.reply(401, {'error': '需要 Authorization: Bearer <token>'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == '/v1/context':
                    self.reply(200, owner.inbox.context())
                elif self.path == '/v1/drafts':
                    self.reply(200, {'drafts': owner.inbox.list()})
                else:
                    self.reply(404, {'error': '接口不存在'})

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise DraftError('请求大小须在 1 到 65536 字节之间', 413)
                    self.connection.settimeout(3)
                    # Consume bounded bodies before rejecting a route/auth.
                    # Closing with unread POST bytes can reset TCP on Windows
                    # and hide the intended 401/404 from a legitimate client.
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise DraftError('请求内容不完整')
                    if not self.authorized():
                        return
                    if self.path != '/v1/drafts':
                        self.reply(404, {'error': '接口不存在'})
                        return
                    payload = json.loads(body.decode('utf-8'))
                    self.reply(201, owner.inbox.submit(payload))
                except DraftError as error:
                    self.reply(error.status, {'error': str(error)})
                except (ValueError, UnicodeError):
                    self.reply(400, {'error': 'JSON 格式无效'})
                except (TimeoutError, OSError):
                    self.close_connection = True

        self._server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self._thread.start()
        return self.url

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=2)
            self._server = self._thread = None
