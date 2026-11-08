"""Transport contracts, replay protection, and recognition stability gates."""
import copy
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from automation_api import DraftInbox, DraftError, LocalDraftServer
from automation_bridge import StableHandGate


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.inbox = DraftInbox(max_pending=3)
        self.inbox.set_context({'schema_version': 1, 'context_id': 'current', 'state': {'phase': 'playing'}})

    def payload(self, **overrides):
        result = dict(schema_version=1, context_id='current', kind='tiles', player=0,
                      source='replaceable-test-provider', request_id='one', data={'tiles': ['1m'] * 3})
        result.update(overrides)
        return result

    def test_submission_is_only_a_draft_and_repeated_request_is_idempotent(self):
        before = self.inbox.context()
        first = self.inbox.submit(self.payload())
        again = self.inbox.submit(self.payload())
        self.assertEqual(first, again)
        self.assertEqual(len(self.inbox.list()), 1)
        self.assertEqual(self.inbox.context(), before)
        first['data']['tiles'].clear()
        self.assertEqual(len(self.inbox.get(first['id'])['data']['tiles']), 3)

    def test_reusing_request_for_other_data_or_old_context_is_rejected(self):
        self.inbox.submit(self.payload())
        with self.assertRaises(DraftError):
            self.inbox.submit(self.payload(player=1))
        self.inbox.set_context({'context_id': 'next', 'state': {'phase': 'playing'}})
        self.assertEqual(self.inbox.list()[0]['status'], 'stale')
        with self.assertRaises(DraftError):
            self.inbox.submit(self.payload(request_id='two'))

    def test_pending_limit_completed_replay_and_resolved_hand(self):
        for i in range(3):
            self.inbox.submit(self.payload(request_id=str(i)))
        with self.assertRaises(DraftError):
            self.inbox.submit(self.payload(request_id='overflow'))
        identity = self.inbox.list()[0]['id']
        self.inbox.mark(identity, 'applied')
        self.assertEqual(self.inbox.submit(self.payload(request_id='0'))['status'], 'applied')
        with self.assertRaises(DraftError):
            self.inbox.get(identity, pending=True)
        self.inbox.set_context({'context_id': 'done', 'state': {'phase': 'result'}})
        with self.assertRaises(DraftError):
            self.inbox.submit(self.payload(context_id='done', request_id='new'))

    def test_rejects_malformed_types_and_bonus_only_win(self):
        for overrides in ({'player': True}, {'schema_version': True}, {'data': {'tiles': ['8z']}},
                          {'kind': 'waits', 'data': {'waits': ['5m', '0m']}},
                          {'kind': 'waits', 'data': {'waits': ['1m'], 'no_yaku': ['2m']}},
                          {'kind': 'win', 'data': {'type': 'ron', 'loser': 1, 'han': 0, 'fu': 30, 'yaku': ['立直']}}):
            with self.subTest(overrides=overrides), self.assertRaises(DraftError):
                self.inbox.submit(self.payload(**overrides))

    def test_concurrent_duplicate_submissions_create_one_draft(self):
        responses = []
        threads = [threading.Thread(target=lambda: responses.append(self.inbox.submit(self.payload()))) for _ in range(12)]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join()
        self.assertEqual(len({d['id'] for d in responses}), 1)

    def test_http_contract_authentication_and_no_commit_route(self):
        server = LocalDraftServer(self.inbox)
        try:
            url = server.start()
            headers = {'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'}
            with self.assertRaises(HTTPError) as error:
                urlopen(url + '/v1/context', timeout=2)
            self.assertEqual(error.exception.code, 401)
            with urlopen(Request(url + '/v1/context', headers=headers), timeout=2) as response:
                context = json.load(response)
            self.assertEqual(context['context_id'], 'current')
            with urlopen(Request(url + '/v1/drafts', data=json.dumps(self.payload()).encode(), headers=headers), timeout=2) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.load(response)['status'], 'pending')
            for _ in range(4):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url + '/v1/settle', data=b'{}', headers=headers), timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + '/v1/drafts', data=b'{}'), timeout=2)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
        finally:
            server.stop()
        self.assertEqual(server.url, '')


class StableGateTests(unittest.TestCase):
    def frame(self, tiles=None, confidence=.9):
        return [SimpleNamespace(tile=t, confidence=confidence) for t in (tiles or ['1m', '2m', '3m'])]

    def test_three_consecutive_frames_and_no_repeat_spam(self):
        gate = StableHandGate()
        self.assertIsNone(gate.observe(self.frame(), 3))
        self.assertIsNone(gate.observe(self.frame(), 3))
        self.assertEqual(gate.observe(self.frame(), 3), ['1m', '2m', '3m'])
        self.assertIsNone(gate.observe(self.frame(), 3))
        gate.reset()
        self.assertIsNone(gate.observe(self.frame(), 3))

    def test_low_confidence_unknown_count_and_five_copies_break_run(self):
        gate = StableHandGate(stable_frames=2)
        gate.observe(self.frame(), 3)
        self.assertIsNone(gate.observe(self.frame(confidence=.2), 3))
        self.assertIsNone(gate.observe(self.frame(), 3))
        self.assertIsNone(gate.observe(self.frame(['1m', None, '3m']), 3))
        self.assertIsNone(gate.observe(self.frame(['1m']), 3))
        self.assertIsNone(gate.observe(self.frame(['5m', '5m']), 2,
                                      melds=[{'tiles': ['0m', '5m', '5m']}]))
        # A river tile can make the total exactly 13 while the concealed hand
        # is missing one. Keep the preview, but never issue an automatic draft.
        standing = [SimpleNamespace(tile=f'{1 + i % 9}m', confidence=.9,
                    bbox=(i * 30, 200 - i * 8, i * 30 + 25, 250 - i * 8)) for i in range(13)]
        standing[0].bbox = (0, 70, 25, 95)
        for _ in range(3):
            self.assertIsNone(gate.observe(standing, 13))
        self.assertFalse(gate.valid_frame)
        self.assertIn('选区', gate.reason)
        standing[0].bbox = (0, 200, 25, 250)
        self.assertIsNone(gate.observe(standing, 13))
        self.assertIsNotNone(gate.observe(standing, 13))


if __name__ == '__main__':
    unittest.main()
