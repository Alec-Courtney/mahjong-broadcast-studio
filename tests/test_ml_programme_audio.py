"""Protect dry live intervals and actual-start-only declaration audio."""
from array import array
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from mix_ml_programme_audio import RATE, inspect_phase_mix, render_phase_mix, validate_schedule


def schedule_data():
    return dict(duration_seconds=45., live_interval=[1., 5.], postmatch_start_seconds=5.,
        music_intervals=[[0., 1.], [5., 45.]],
        riichi_effect_starts=[dict(sequence_id=1, time_s=1.2, duration_seconds=2.5)])


class ProgrammeAudioTests(unittest.TestCase):
    def test_music_is_rejected_even_when_only_a_small_part_overlaps_a_live_hand(self):
        data = schedule_data(); data['music_intervals'][0][1] = 1.01
        with self.assertRaisesRegex(ValueError, 'overlap'): validate_schedule(data)

    def test_duplicate_starts_and_overlapping_fifo_items_are_rejected(self):
        for cue in (dict(sequence_id=1, time_s=3.8, duration_seconds=2.5),
                    dict(sequence_id=2, time_s=2., duration_seconds=2.5)):
            with self.subTest(cue=cue):
                data = schedule_data(); data['riichi_effect_starts'].append(cue)
                with self.assertRaises(ValueError): validate_schedule(data)
        valid = validate_schedule(schedule_data())
        self.assertEqual([cue['time_s'] for cue in valid['cues']], [1.2])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'The optional offline audio mixer needs ffmpeg')
    def test_encoded_mix_is_dry_between_effects_and_proof_rejects_a_hidden_live_tone(self):
        def write(path, samples):
            with wave.open(str(path), 'wb') as target:
                target.setnchannels(2); target.setsampwidth(2); target.setframerate(RATE)
                target.writeframes(samples.tobytes())
        with tempfile.TemporaryDirectory(prefix='tmp-programme-audio-', dir=ROOT/'tests') as temporary:
            folder = Path(temporary); music = folder/'music.wav'; post = folder/'post.wav'; riichi = folder/'riichi.wav'
            write(music, array('h', [1000, -1000])*round(.2*RATE))
            write(post, array('h', [2000, -2000])*round(.1*RATE))
            cue = array('h', [0, 0])*round(2.5*RATE)
            for frame in range(round(.46*RATE), round(1.36*RATE)):
                cue[frame*2] = 3000; cue[frame*2+1] = -3000
            write(riichi, cue)
            schedule = dict(duration=6., live=(1., 4.), post=4.,
                intervals=[(0., 1.), (4., 6.)], cues=[dict(sequence_id=1, time_s=1.2)])
            mixed = render_phase_mix(music, post, riichi, folder/'mixed.wav', schedule)
            proof = inspect_phase_mix(mixed, music, post, riichi, schedule)
            self.assertEqual(proof['dry_live_peak_pcm_lsb'], 0)
            self.assertEqual(proof['riichi_actual_start_count'], 1)
            self.assertGreater(proof['audible_live_cue_samples'], 0)
            with wave.open(str(mixed), 'rb') as source:
                changed = array('h'); changed.frombytes(source.readframes(source.getnframes()))
            changed[round(1.05*RATE)*2] = 512
            write(mixed, changed)
            with self.assertRaisesRegex(RuntimeError, 'PCM verification failed'):
                inspect_phase_mix(mixed, music, post, riichi, schedule)


if __name__ == '__main__': unittest.main()
