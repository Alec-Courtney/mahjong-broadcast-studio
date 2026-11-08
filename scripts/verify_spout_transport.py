r"""Opt-in native Spout self-check; synthetic sources, no OBS/camera required.

Run from the project with .venv\Scripts\python scripts\verify_spout_transport.py.
The temporary senders are removed in finally; existing sender bindings are not
changed. Results describe transport acceptance, not OBS switching acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def run():
    import numpy as np
    import SpoutGL
    from spout_capture import SpoutCapture, enumerate_senders

    prefix = 'MahjongSelfCheck_' + uuid.uuid4().hex[:8] + '_'
    names = [prefix + str(seat + 1) for seat in range(4)]
    stops = [threading.Event() for _ in range(4)]
    pauses = [threading.Event() for _ in range(4)]
    sizes = [(192, 128)] * 4
    reader_stops = [threading.Event() for _ in range(5)]
    records = [[] for _ in range(5)]
    statuses = [''] * 5
    errors = []
    threads = []
    active_reader = SpoutGL.SpoutReceiver()
    previous_active = active_reader.getActiveSender()

    def sender(seat):
        source = SpoutGL.SpoutSender()
        try:
            if not source.createOpenGL():
                raise RuntimeError('native sender GL initialization failed')
            source.setSenderName(names[seat])
            frame_count = 0
            while not stops[seat].is_set():
                if not pauses[seat].is_set():
                    width, height = sizes[seat]
                    rgba = np.zeros((height, width, 4), dtype=np.uint8)
                    rgba[:, :] = (30 + seat * 45, 70, 110, 255)
                    rgba[:height // 2, :, 1] = 160
                    rgba[0, 0, 2] = frame_count % 255
                    if not source.sendImage(rgba, width, height, SpoutGL.enums.GL_RGBA, False, 0):
                        raise RuntimeError('native sender failed to send')
                    frame_count += 1
                stops[seat].wait(.035)
        except Exception as error:
            errors.append(f'sender {seat}: {error}')
        finally:
            source.releaseSender()
            source.closeOpenGL()

    def receive(index):
        seat = index % 4
        try:
            with SpoutCapture(names[seat]) as reader:
                while not reader_stops[index].is_set():
                    okay, frame = reader.read()
                    statuses[index] = reader.status
                    if okay:
                        top = tuple(int(v) for v in frame[10, 10])
                        bottom = tuple(int(v) for v in frame[-10, 10])
                        if top != (110, 160, 30 + seat * 45) or bottom != (110, 70, 30 + seat * 45):
                            raise AssertionError(f'wrong sender/color/orientation: {top}, {bottom}')
                        records[index].append((time.monotonic(), reader.sender_frame, frame.shape[:2]))
        except Exception as error:
            errors.append(f'receiver {index}: {error}')

    def wait_for(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while not predicate():
            if errors:
                raise AssertionError(errors)
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Spout self-check timed out: counts={[len(r) for r in records]}, statuses={statuses}')
            time.sleep(.03)

    started = time.monotonic()
    try:
        for seat in range(4):
            thread = threading.Thread(target=sender, args=(seat,), daemon=True)
            threads.append(thread)
            thread.start()
        wait_for(lambda: all(name in enumerate_senders() for name in names))
        for index in range(5):
            thread = threading.Thread(target=receive, args=(index,), daemon=True)
            threads.append(thread)
            thread.start()
        wait_for(lambda: all(len(items) >= 4 for items in records))
        before_switch = [len(items) for items in records]
        for name in reversed(names):
            active_reader.setActiveSender(name)
            time.sleep(.12)
        wait_for(lambda: all(len(items) >= before_switch[i] + 4 for i, items in enumerate(records)))
        reader_stops[4].set()
        threads[8].join(3)
        assert not threads[8].is_alive(), 'preview did not close'
        after_preview = [len(items) for items in records[:4]]
        wait_for(lambda: all(len(records[i]) > after_preview[i] for i in range(4)))

        pauses[0].set()
        time.sleep(.35)
        frozen_count = len(records[0])
        time.sleep(.4)
        assert len(records[0]) == frozen_count, 'frozen sender generated duplicate frames'
        pauses[0].clear()
        wait_for(lambda: len(records[0]) > frozen_count)
        sizes[1] = (256, 144)
        wait_for(lambda: records[1][-1][2] == (144, 256))

        # Fully remove one sender and recreate it under the same pinned name.
        stops[2].set()
        threads[2].join(3)
        assert not threads[2].is_alive(), 'sender did not close'
        time.sleep(.35)
        gap_count = len(records[2])
        time.sleep(.3)
        assert len(records[2]) == gap_count, 'disconnected sender leaked an active sender'
        stops[2].clear()
        thread = threading.Thread(target=sender, args=(2,), daemon=True)
        threads.append(thread)
        thread.start()
        wait_for(lambda: len(records[2]) > gap_count)
        return {
            'native_spout_tested': True, 'obs_tested': False, 'physical_camera_tested': False,
            'sender_count': 4, 'concurrent_receivers': 5,
            'frames_per_receiver': [len(items) for items in records],
            'fixed_names_despite_active_sender_changes': True,
            'preview_close_keeps_four_receivers': True,
            'stalled_frames_rejected': True, 'resize_recovered': True,
            'sender_restart_recovered': True, 'rgba_to_bgr_orientation_verified': True,
            'elapsed_seconds': round(time.monotonic() - started, 2), 'errors': errors,
        }
    finally:
        for event in reader_stops + stops:
            event.set()
        for thread in threads:
            thread.join(4)
        if previous_active and previous_active in enumerate_senders():
            active_reader.setActiveSender(previous_active)
        active_reader.releaseReceiver()


if __name__ == '__main__':
    result = run()
    output = ROOT / 'previews' / 'checks' / 'spout-transport' / 'native-verification.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
