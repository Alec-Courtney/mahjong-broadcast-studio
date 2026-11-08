"""Declaration sound follows actual FIFO starts, never clicks or repainting."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from model import Model
from overlays import OverlayManager

APP=QApplication.instance() or QApplication([])


class FakeRiichiAudio:
    def __init__(self):self.calls=[]
    def play(self,sequence_id):self.calls.append(('play',sequence_id));return True
    def stop(self):self.calls.append(('stop',));return True
    def close(self):self.calls.append(('close',));return True
    @property
    def played(self):return [call[1] for call in self.calls if call[0]=='play']


class RiichiAudioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tmp-riichi-cue-',dir=ROOT/'tests')
        self.model=Model(self.tmp.name);self.clock=patch('overlays.time.monotonic',return_value=100.)
        self.now=self.clock.start();self.audio=FakeRiichiAudio()
        self.manager=OverlayManager(self.model,riichi_audio=self.audio)

    def tearDown(self):
        self.manager.close();APP.processEvents();self.clock.stop();self.tmp.cleanup()

    def test_fifo_sound_starts_once_per_actual_item_and_not_when_it_is_queued(self):
        self.model.riichi(0);self.now.return_value=100.2;self.model.riichi(1)
        self.assertEqual(self.audio.played,[1])
        for now in (100.4,101.,102.,102.49):
            self.now.return_value=now;self.manager.tick();self.manager.on_display()
        self.assertEqual(self.audio.played,[1])
        self.now.return_value=102.5;self.manager.tick();self.assertEqual(self.audio.played,[1,2])
        for _ in range(5):self.manager.tick();self.manager.on_display()
        self.assertEqual(self.audio.played,[1,2])
        self.now.return_value=105.;self.manager.tick();self.assertEqual(self.audio.calls[-1],('stop',))

    def test_cancelled_pending_item_is_silent_and_cancelled_active_starts_only_next_item(self):
        self.model.riichi(0);self.model.riichi(1);self.model.riichi(2)
        self.model.riichi(1);self.assertEqual(self.audio.played,[1])
        self.model.riichi(0);self.assertEqual(self.audio.played,[1,3])
        self.manager.tick();self.assertEqual(self.audio.played,[1,3])

    def test_global_hide_and_new_hand_stop_sound_without_restarting_old_declarations(self):
        self.model.riichi(0);self.manager.set_overlays_visible(False)
        self.assertEqual(self.audio.calls[-1],('stop',));self.assertFalse(self.manager.riichi_queue.busy)
        self.manager.set_overlays_visible(True);self.assertEqual(self.audio.played,[1])
        self.model.riichi(1);self.assertEqual(self.audio.played,[1,2])
        self.model.start_match();self.assertFalse(self.manager.riichi_queue.busy)
        self.assertEqual(self.audio.calls[-1],('stop',))

    def test_settlement_stops_current_sound_and_device_lifecycle_follows_manager(self):
        self.model.riichi(0);self.model.settle(dict(type='ron',winner=1,loser=2,han=1,fu=30),None)
        self.assertEqual(self.audio.calls[-1],('stop',));self.assertFalse(self.manager.riichi_queue.busy)
        self.manager.close();self.assertIn(('close',),self.audio.calls)


if __name__=='__main__':unittest.main()
