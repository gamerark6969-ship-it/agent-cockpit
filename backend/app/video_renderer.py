"""Portable, CPU-only illustrated story renderer. No paid media API required."""

import asyncio
import base64
import importlib.util
import json
import math
import os
import shutil
import struct
import wave
from functools import lru_cache
from pathlib import Path


def _ffmpeg() -> str:
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def renderer_status() -> dict:
    missing = [name for name in ("PIL", "imageio_ffmpeg", "edge_tts") if importlib.util.find_spec(name) is None]
    return {"available": not missing, "detail": "Install " + ", ".join(missing) if missing else "CPU illustrated animation; free speech with offline espeak fallback"}


def validate_manifest(manifest: dict) -> list[dict]:
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
        raise ValueError("A video needs 1–12 scenes")
    for scene in scenes:
        if not isinstance(scene, dict) or not isinstance(scene.get("narration"), str) or not scene["narration"].strip():
            raise ValueError("Every scene needs narration")
        if len(scene["narration"]) > 3000:
            raise ValueError("Scene narration is too long")
    return scenes


async def _command(*args: str, timeout: int = 120) -> bytes:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, errors = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise RuntimeError("Media command failed: " + errors.decode(errors="replace")[-400:])
        return output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def validate_music(path: Path) -> None:
    samples = await _command(_ffmpeg(), "-v", "error", "-xerror", "-f", "mp3",
                             "-i", str(path), "-map", "0:a:0", "-t", "60",
                             "-ac", "1", "-ar", "8000", "-f", "s16le", "-", timeout=30)
    if len(samples) < 1600:
        raise ValueError("Track contains less than 100 ms of audio")


async def _speech(text: str, target: Path, voice: str) -> tuple[float, list[dict], str]:
    """Produce real narration; never substitute silence after provider failure."""
    words = []
    source = target.with_suffix(".mp3")
    try:
        if os.environ.get("VIDEO_TTS_MODE") == "offline":
            raise RuntimeError("Offline speech requested")
        import edge_tts
        async with asyncio.timeout(75):
            with source.open("wb") as audio:
                async for event in edge_tts.Communicate(text, voice, rate="+8%", boundary="WordBoundary").stream():
                    if event["type"] == "audio":
                        audio.write(event["data"])
                    elif event["type"] == "WordBoundary":
                        words.append({"text": event["text"], "start": event["offset"] / 10_000_000, "duration": event["duration"] / 10_000_000})
        await _command(_ffmpeg(), "-y", "-i", str(source), "-ar", "24000", "-ac", "1", str(target))
        method = "edge-tts"
    except (Exception, TimeoutError):
        executable = shutil.which("espeak-ng") or shutil.which("espeak")
        if executable:
            language = voice.split("-",1)[0]
            await _command(executable, "-v", language, "-s", "175", "-w", str(target), "--", text)
            method = "espeak-approximate-timing"
        elif os.name == "nt" and shutil.which("powershell.exe"):
            payload = base64.b64encode(json.dumps({"text":text,"output":str(target)}).encode()).decode()
            script = Path(__file__).resolve().parents[1] / "scripts" / "sapi_speech.ps1"
            await _command("powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script), "-Payload", payload)
            method = "windows-sapi-approximate-timing"
        else:
            raise RuntimeError("Speech service failed and espeak-ng is not installed") from None
        words = []
    with wave.open(str(target), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
        samples = audio.readframes(audio.getnframes())
    if duration < 0.3:
        raise RuntimeError("Narration is empty")
    if not samples or max(abs(value[0]) for value in struct.iter_unpack("<h", samples)) < 30:
        raise RuntimeError("Narration contains no audible speech")
    if not words:
        tokens = text.split()
        words = [{"text": token, "start": i * duration / len(tokens), "duration": duration / len(tokens)} for i, token in enumerate(tokens)]
    return duration, words, method


def _score(path: Path, duration: float) -> None:
    """Quiet original synthesized score, generated from notes rather than samples."""
    sample_rate = 12000
    notes = (130.81,164.81,196.0,146.83,174.61,220.0)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1,2,sample_rate,0,"NONE","not compressed"))
        for start in range(0,math.ceil(duration*sample_rate),sample_rate):
            block = bytearray()
            for frame in range(start,min(start+sample_rate,math.ceil(duration*sample_rate))):
                t = frame/sample_rate
                frequency = notes[int(t/4)%len(notes)]
                swell = .6 + .4*math.sin(math.pi*(t%4)/4)
                value = (math.sin(2*math.pi*frequency*t) + .35*math.sin(2*math.pi*frequency*1.5*t))*200*swell
                block.extend(struct.pack("<h",int(value)))
            audio.writeframes(block)


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0,round(seconds*1000))
    hours, rest = divmod(milliseconds,3600000)
    minutes, rest = divmod(rest,60000)
    seconds, millis = divmod(rest,1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


@lru_cache(maxsize=24)
def _font(size: int):
    from PIL import ImageFont
    candidates = [os.environ.get("VIDEO_FONT", ""), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        proposal = f"{current} {word}".strip()
        if current and draw.textlength(proposal, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = proposal
    return [*lines, current] if current else lines


def _frame(scene: dict, title: str, t: float, duration: float, words: list[dict], width: int, height: int, number: int, total: int):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    from .video_scenes import draw_background, draw_prop
    draw_background(draw, scene, t, width, height)
    draw_prop(draw, scene, t, width, height)
    # Two expressive block characters; positions and gestures animate per scene.
    for actor in range(2):
        action = scene.get("action", "walk")
        movement = math.sin(t * (.9 if action in ("run", "escape") else .4) + number) * width * .035
        if actor == 0 and action in ("run", "escape"):
            movement += width * .12 * min(1,t/max(duration,.1))
        x = width * (.30 + actor * .28) + movement
        bounce = .012 if action in ("run", "celebrate") else .005
        y = height * .51 + math.sin(t * 4 + actor) * height * bounce
        size = width * (.13 if actor == 0 else .1)
        color = (102, 215, 189) if actor == 0 else (255, 173, 109)
        draw.line((x-size*.4, y+size*.9, x-size*.55, y+size*1.6), fill=color, width=max(2,width//35))
        draw.line((x+size*.4, y+size*.9, x+size*.55, y+size*1.6), fill=color, width=max(2,width//35))
        draw.rounded_rectangle((x-size*.65,y, x+size*.65,y+size), radius=width//40, fill=color)
        draw.rounded_rectangle((x-size*.65,y-size*1.25,x+size*.65,y-size*.1), radius=width//35, fill=(244,209,176))
        for eye in (-.25,.25):
            draw.ellipse((x+size*eye-size*.06,y-size*.8,x+size*eye+size*.06,y-size*.66),fill=(20,27,42))
        if scene.get("emotion") == "surprised":
            draw.ellipse((x-size*.1,y-size*.52,x+size*.1,y-size*.3),outline=(65,39,49),width=max(1,width//150))
        else:
            start, end = (180,360) if scene.get("emotion") == "worried" else (0,180)
            draw.arc((x-size*.25,y-size*.62,x+size*.25,y-size*.35),start,end,fill=(65,39,49),width=max(1,width//150))
        if action in ("celebrate", "discover", "talk"):
            direction = -1 if actor == 0 else 1
            draw.line((x+direction*size*.55,y+size*.2,x+direction*size,y-size*.3),fill=color,width=max(2,width//40))
    font = _font(max(12, width // 19))
    small = _font(max(10, width // 28))
    draw.text((width*.07,height*.065),f"STORY {number + 1:02d} / {total:02d}",font=small,fill=(119,222,197))
    for index, line in enumerate(_wrap(draw,title,font,int(width*.86))[:3]):
        draw.text((width*.07,height*.1+index*width*.065),line,font=font,fill="white")
    active = max((i for i,w in enumerate(words) if w["start"] <= t), default=0)
    caption = " ".join(w["text"] for w in words[active//4*4:active//4*4+4])
    lines = _wrap(draw,caption,font,int(width*.8))[:3]
    for index,line in enumerate(lines):
        y = height*.76+index*width*.08
        draw.text((width/2,y),line,font=font,fill="white",anchor="mm",stroke_width=max(1,width//150),stroke_fill=(7,12,23))
    draw.rounded_rectangle((width*.08,height*.91,width*.92,height*.914),radius=2,fill=(55,72,89))
    progress = (number + t / max(duration,.1)) / total
    draw.rectangle((width*.08,height*.91,width*(.08+.84*progress),height*.914),fill=(119,222,197))
    return image


async def render_video(manifest: dict, output_dir: Path, *, voice: str = "en-US-AriaNeural", music_path: Path | None = None) -> dict:
    scenes = validate_manifest(manifest)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    width = int(os.environ.get("VIDEO_WIDTH", "1080"))
    height = int(os.environ.get("VIDEO_HEIGHT", "1920"))
    fps = int(os.environ.get("VIDEO_FPS", "24"))
    if width % 2 or height % 2 or not 96 <= width <= 1080 or not 96 <= height <= 1920 or not 1 <= fps <= 30:
        raise ValueError("Invalid video dimensions or frame rate")
    semaphore = asyncio.Semaphore(2)

    async def narrate(index, scene):
        async with semaphore:
            return await _speech(scene["narration"], output_dir / f"voice-{index}.wav", voice)

    audio = await asyncio.gather(*(narrate(i,s) for i,s in enumerate(scenes)))
    raw_duration = sum(item[0] for item in audio)
    if raw_duration > 60:
        raise ValueError("Narration exceeds 60 seconds; shorten the script")
    duration = max(30.0, raw_duration)
    target = output_dir / "video.mp4"
    thumbnail = output_dir / "thumbnail.jpg"
    timeline, offset = [], 0.0
    for scene, (length, words, method) in zip(scenes,audio):
        timeline.append({"start":offset,"duration":length,"words":words,"voice_method":method})
        offset += length
    timeline[-1]["duration"] += duration - raw_duration
    subtitles = []
    for timing in timeline:
        for index in range(0,len(timing["words"]),4):
            group = timing["words"][index:index+4]
            start = timing["start"] + group[0]["start"]
            end = timing["start"] + group[-1]["start"] + group[-1]["duration"]
            subtitles.append(f"{len(subtitles)+1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{' '.join(w['text'] for w in group)}\n")
    (output_dir / "captions.srt").write_text("\n".join(subtitles),encoding="utf-8")
    if music_path is None:
        await asyncio.to_thread(_score,output_dir / "score.wav",duration)
    (output_dir / "manifest.json").write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
    (output_dir / "timeline.json").write_text(json.dumps(timeline,ensure_ascii=False),encoding="utf-8")
    labels = "".join(f"[{i+1}:a]" for i in range(len(audio)))
    command = [_ffmpeg(),"-y","-loglevel","error","-f","rawvideo","-pix_fmt","rgb24",
               "-s",f"{width}x{height}","-r",str(fps),"-i","pipe:0"]
    for i in range(len(audio)):
        command += ["-i",str(output_dir / f"voice-{i}.wav")]
    music_input = f"[{len(audio)+1}:a]"
    if music_path is None:
        command += ["-i", str(output_dir / "score.wav")]
        music_filter = ""
    else:
        command += ["-stream_loop", "-1", "-f", "mp3", "-i", str(music_path)]
        music_filter = f"{music_input}atrim=duration={duration},volume=0.12[bed];"
        music_input = "[bed]"
    command += ["-filter_complex", f"{labels}concat=n={len(audio)}:v=0:a=1[voice];{music_filter}[voice]{music_input}amix=inputs=2:duration=longest:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=9,apad[a]",
                "-map","0:v","-map","[a]","-t",str(duration),"-c:v","libx264","-preset","veryfast",
                "-crf","21","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart",str(target)]
    process = await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(1200):
            scene_index = 0
            for frame in range(math.ceil(duration*fps)):
                clock = frame/fps
                while scene_index+1 < len(timeline) and clock >= timeline[scene_index+1]["start"]:
                    scene_index += 1
                timing = timeline[scene_index]
                image = _frame(scenes[scene_index],manifest.get("title","Original story"),clock-timing["start"],
                               timing["duration"],timing["words"],width,height,scene_index,len(scenes))
                if frame == 0:
                    image.save(thumbnail,quality=92)
                process.stdin.write(image.tobytes())
                await process.stdin.drain()
            process.stdin.close()
            errors = await process.stderr.read()
            await process.wait()
            if process.returncode:
                raise RuntimeError("Video encoding failed: " + errors.decode(errors="replace")[-300:])
        await _command(_ffmpeg(),"-v","error","-i",str(target),"-f","null","-",timeout=180)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    checks = {"music": "uploaded" if music_path else "generated", "full_decode":"passed","audio":"real_narration","caption_timing":[a[2] for a in audio],
              "fps":fps,"renderer":"procedural-illustrated-v1"}
    return {"path":str(target),"thumbnail_path":str(thumbnail),"duration_seconds":duration,
            "width":width,"height":height,"checks":checks}
