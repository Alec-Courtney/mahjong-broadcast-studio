"""Minimal replaceable producer: submit a JSON suggestion using only stdlib.

Example: python scripts/automation_client.py --url http://127.0.0.1:PORT
         --token TOKEN --player 0 --tiles 123m123p123s1112z
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen, build_opener, ProxyHandler


def submit_draft(url, token, payload):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
        raise ValueError('请使用导播台显示的 http://127.0.0.1:端口 地址')
    opener = build_opener(ProxyHandler({}))
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    with opener.open(Request(url.rstrip('/') + '/v1/context', headers=headers), timeout=5) as response:
        context = json.load(response)
    request = dict(payload)
    request.setdefault('schema_version', 1)
    request.setdefault('context_id', context['context_id'])
    request.setdefault('source', 'external-client')
    request.setdefault('request_id', uuid.uuid4().hex)
    body = json.dumps(request, ensure_ascii=False).encode('utf-8')
    with opener.open(Request(url.rstrip('/') + '/v1/drafts', data=body, headers=headers), timeout=5) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description='向导播台提交草稿；不会直接播出或结算')
    parser.add_argument('--url', required=True)
    parser.add_argument('--token', default=os.environ.get('MAHJONG_AUTOMATION_TOKEN', ''))
    parser.add_argument('--payload', type=Path, help='完整 JSON 草稿文件；无需填写 context_id')
    parser.add_argument('--player', type=int, choices=range(4), default=0)
    parser.add_argument('--tiles', help='紧凑暗手牌，例如 123m123p123s1112z')
    options = parser.parse_args()
    if not options.token:
        parser.error('请指定 --token 或 MAHJONG_AUTOMATION_TOKEN')
    if options.payload:
        payload = json.loads(options.payload.read_text(encoding='utf-8-sig'))
    elif options.tiles:
        # Tile parsing is deliberately local to this example, so remote
        # recognizers need no import from the original application.
        import re
        text = re.sub(r'[\s,，]', '', options.tiles.lower())
        if not re.fullmatch(r'(?:[0-9]+[mps]|[1-7]+z)+', text):
            parser.error('牌面格式无效')
        tiles = [n + suit for digits, suit in re.findall(r'([0-9]+)([mpsz])', text) for n in digits]
        payload = {'kind': 'tiles', 'player': options.player, 'data': {'tiles': tiles}}
    else:
        parser.error('请指定 --payload 或 --tiles')
    print(json.dumps(submit_draft(options.url, options.token, payload), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
