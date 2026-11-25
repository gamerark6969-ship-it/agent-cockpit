"""Render and decode an original fixture using real speech, without publishing it."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0,str(ROOT))

from app.video_renderer import render_video

STORY = {
    "version":1,
    "title":"The Key That Opened Tomorrow",
    "description":"An original illustrated story about a very unusual lost key.",
    "tags":["animation","originalstory"],
    "scenes":[
        {"narration":"Every morning, Nia found the same tiny key on her doorstep. Every evening, it vanished before she could use it.","visual":"A town doorstep at sunrise", "setting":"town", "action":"discover", "emotion":"surprised", "prop":"key"},
        {"narration":"Her friend suggested the old observatory. They ran through the forest, following a trail of small golden footprints.","visual":"Friends follow a forest trail", "setting":"forest", "action":"run", "emotion":"determined", "prop":"star"},
        {"narration":"Inside, a clock had no hands. Beneath it, a locked door carried one strange instruction: Come back yesterday.","visual":"A strange room with an old clock", "setting":"room", "action":"discover", "emotion":"worried", "prop":"clock"},
        {"narration":"Nia laughed. She turned the key backward, and the door opened onto a sky filled with tomorrow's stars.","visual":"The key reveals a starry sky", "setting":"space", "action":"discover", "emotion":"surprised", "prop":"door"},
        {"narration":"She left a note for her future self: Remember to return the key. That explained every morning on her doorstep.","visual":"Friends celebrate a solved mystery", "setting":"town", "action":"celebrate", "emotion":"happy", "prop":"key"},
    ],
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/smoke-video"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--generate", action="store_true", help="Generate a new original script using the configured model")
    parser.add_argument("--model", default="freegg/gpt-6.1-sol")
    parser.add_argument("--full-size", action="store_true")
    parser.add_argument("--save-studio",action="store_true",help="Save the real draft to Studio as a paused private test series")
    args = parser.parse_args()
    os.environ.update({"VIDEO_WIDTH":"1080" if args.full_size else "360",
                       "VIDEO_HEIGHT":"1920" if args.full_size else "640", "VIDEO_FPS":"24" if args.full_size else "12"})
    if args.offline:
        os.environ["VIDEO_TTS_MODE"] = "offline"
    async def execute():
        story = STORY
        run_id = None
        output = args.output
        if args.save_studio:
            from app.main import run_migrations
            from app.models import new_id
            from app.automation.storage import run_directory
            await asyncio.to_thread(run_migrations)
            run_id = new_id()
            output = run_directory(run_id)
        if args.generate:
            from app.agent.provider_store import refresh_provider_routes
            from app.automation.service import AutomationService
            from app.db import engine
            try:
                await refresh_provider_routes()
                story = await AutomationService()._generate(SimpleNamespace(id="smoke-series", model=args.model, language="en",
                    brief="Original imaginative microadventures starring two friends Nia and Keo. A gentle, clever mystery with a clear rule and a satisfying funny ending. Use the illustrated town, forest, room, ocean and space sets; avoid aging or time-travel stories."), "smoke-run")
            finally:
                await engine.dispose()
        result = await render_video(story,output)
        if args.save_studio:
            from app.automation.models import VideoRun, VideoRunEvent, VideoSeries
            from app.automation.storage import archive_media
            from app.db import SessionLocal, engine
            archived = await archive_media(run_id,result)
            try:
                async with SessionLocal() as session:
                    series = VideoSeries(name="Studio commissioning test",brief="An original illustrated story generated to verify the complete production pipeline.",
                        model=args.model if args.generate else None,enabled=False,publish_mode="review",privacy="private")
                    session.add(series)
                    await session.flush()
                    session.add(VideoRun(id=run_id,series_id=series.id,status="review",stage="upload",title=story["title"],description=story["description"],
                        manifest=story,render_data={**result,**archived},media_path=result["path"],thumbnail_path=result["thumbnail_path"]))
                    session.add(VideoRunEvent(run_id=run_id,type="review",message="Real rendered video passed decoding and narration checks; ready for commissioning review."))
                    await session.commit()
                    print("Studio draft saved:",run_id)
            finally:
                await engine.dispose()
        print("Video title:",story["title"])
        return result
    result = asyncio.run(execute())
    print(json.dumps(result,indent=2))
