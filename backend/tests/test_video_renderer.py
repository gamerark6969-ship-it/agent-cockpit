import math
import os
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.video_renderer import render_video, validate_manifest


class VideoRendererTests(unittest.IsolatedAsyncioTestCase):
    def test_bad_story_is_rejected(self):
        for manifest in ({}, {"scenes":[]}, {"scenes":[{"narration":""}]}):
            with self.assertRaises(ValueError):
                validate_manifest(manifest)

    async def test_real_mp4_encoding_and_full_decode(self):
        # A tone stands in for the network speech provider in this encoding test.
        async def fixture_speech(text, path, voice):
            with wave.open(str(path),"wb") as audio:
                audio.setparams((1,2,8000,0,"NONE","not compressed"))
                audio.writeframes(b"".join(struct.pack("<h",int(5000*math.sin(2*math.pi*220*i/8000))) for i in range(8000)))
            return 1.0,[{"text":"Test","start":0.0,"duration":1.0}],"test-fixture"
        with TemporaryDirectory() as directory, patch.dict(os.environ,{"VIDEO_WIDTH":"160","VIDEO_HEIGHT":"288","VIDEO_FPS":"2"}), patch("app.video_renderer._speech",fixture_speech):
            result = await render_video({"title":"An original story","scenes":[{"narration":"This is a test scene.","visual":"A quiet town"}]},Path(directory))
            self.assertGreater(Path(result["path"]).stat().st_size,1000)
            self.assertTrue(Path(result["thumbnail_path"]).is_file())
            self.assertEqual(result["checks"]["full_decode"],"passed")
            self.assertEqual(result["duration_seconds"],30)
