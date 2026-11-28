"""Select an uploaded soundtrack using the positions in a user-selected playlist."""

import re
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import select

from . import youtube
from .models import ChartTrack, VideoSeries


def parse_playlist(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "://" in value:
        url = urlsplit(value)
        if url.scheme != "https" or url.hostname not in {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"}:
            raise ValueError("Invalid playlist URL")
        value = parse_qs(url.query).get("list", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", value):
        raise ValueError("Invalid playlist ID")
    return value


async def read_chart(session, playlist: str):
    token = await youtube._access_token(session)
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(f"{youtube.YT_API}/playlistItems", headers={"Authorization": f"Bearer {token}"},
                                    params={"playlistId": playlist, "part": "snippet", "maxResults": 50,
                                            "fields": "items(snippet(position,resourceId/videoId))"})
        response.raise_for_status()
        rows = response.json()["items"]
    return sorted((row["snippet"]["position"] + 1, row["snippet"]["resourceId"]["videoId"]) for row in rows)


async def select_music(session, series_id):
    playlist, name = (await session.execute(select(VideoSeries.chart_playlist, VideoSeries.music_name)
                                            .where(VideoSeries.id == series_id))).one()
    reason = ""
    if playlist:
        ids = set(await session.scalars(select(ChartTrack.video_id).where(ChartTrack.series_id == series_id)))
        if ids:
            try:
                ranking = await read_chart(session, playlist)
            except (RuntimeError, httpx.HTTPError, ValueError, KeyError, TypeError):
                reason = "Chart unavailable. "
            else:
                reason = "No licensed match in the first 50 chart entries. "
                for rank, video_id in ranking:
                    if video_id in ids:
                        row = (await session.execute(select(ChartTrack.name, ChartTrack.audio).where(
                            ChartTrack.series_id == series_id, ChartTrack.video_id == video_id))).first()
                        if row:
                            return row.audio, f"Chart #{rank}: {row.name} ({video_id})."
        else:
            reason = "No licensed chart tracks configured. "
    audio = await session.scalar(select(VideoSeries.music_data).where(VideoSeries.id == series_id))
    return audio, reason + (f"Using series track {name}." if audio else "Using generated score.")
