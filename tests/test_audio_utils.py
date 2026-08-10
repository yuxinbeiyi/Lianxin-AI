import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import audio_utils


class PydubFfmpegTests(unittest.TestCase):
    def test_configures_ffmpeg_from_active_conda_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_root = Path(temp_dir)
            bin_dir = env_root / "Library" / "bin"
            bin_dir.mkdir(parents=True)
            ffmpeg = bin_dir / "ffmpeg.exe"
            ffprobe = bin_dir / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()

            with patch("config.get_tts_config", return_value={}), \
                 patch.object(audio_utils.sys, "prefix", str(env_root)), \
                 patch.object(audio_utils.shutil, "which", return_value=None):
                resolved = audio_utils._configure_pydub_ffmpeg()

            self.assertEqual(resolved, str(ffmpeg))

    def test_qq_voice_conversion_forces_edge_tts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            silk_path = str(Path(temp_dir) / "reply.silk")
            with patch.object(audio_utils, "tts_to_wav") as tts_to_wav, \
                 patch.object(audio_utils, "wav_to_silk"):
                self.assertTrue(audio_utils.convert_text_to_voice("测试", silk_path))

            self.assertEqual(tts_to_wav.call_args.kwargs["engine"], "edge_tts")


if __name__ == "__main__":
    unittest.main()
