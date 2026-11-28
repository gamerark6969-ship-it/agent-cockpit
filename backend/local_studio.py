"""Serve the built phone interface and local workers on one port."""

import os
import socket
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles


class PhoneFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Client-side navigation needs index.html even when opened via a deep link.
        section = scope['path'].strip('/').partition('/')[0]
        if section in {'', 'login', 'studio', 'projects', 'tasks', 'c', 'approvals',
                       'connectors', 'reminders', 'settings'}:
            path = 'index.html'
        response = await super().get_response(path, scope)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        return response


def with_phone_ui(api, directory):
    frontend = Starlette(routes=[Mount('/', app=PhoneFiles(directory=directory))])

    async def application(scope, receive, send):
        section = scope.get('path', '').lstrip('/').partition('/')[0]
        if scope['type'] == 'http' and section not in {'api', 's', 'docs', 'redoc', 'openapi.json'}:
            await frontend(scope, receive, send)
        else:
            # The API owns startup, shutdown, authenticated routes and signed previews.
            await api(scope, receive, send)

    return application


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    os.chdir(root)
    load_dotenv(root / '.env')
    if not os.environ.get('APP_TOKEN') or os.environ['APP_TOKEN'] == 'change-me':
        raise SystemExit('Set a private APP_TOKEN in backend/.env before starting the phone interface.')
    # A cloud deployment may have populated the environment on this machine.
    # Keep this launcher's queue, media, and compute on the local host.
    if not os.environ.get('DATABASE_URL', '').startswith('sqlite+aiosqlite:'):
        os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///./app.db'
    os.environ.update(SANDBOX_BACKEND='docker', BACKGROUND_WORKER_ENABLED='true',
                      AUTOMATION_ENABLED='true', AUTOMATION_STORAGE_PERSISTENT='true', KEEPALIVE_ENABLED='false',
                      AUTOMATION_S3_ENDPOINT='', AUTOMATION_GITHUB_REPOSITORY='', AUTOMATION_PUBLIC_BASE_URL='',
                      PUBLIC_BASE_URL='', OPENCODE_REMOTE_WORKER='false', LOCAL_LLM_REMOTE_WORKER='false')
    port = int(os.getenv('PORT', '8000'))
    origin = f'http://localhost:{port}'
    if not os.getenv('AUTOMATION_YOUTUBE_REDIRECT_URI'):
        os.environ['AUTOMATION_YOUTUBE_REDIRECT_URI'] = origin + '/api/automation/youtube/callback'
    if not os.getenv('AUTOMATION_YOUTUBE_CLIENT_FILE'):
        os.environ['AUTOMATION_YOUTUBE_CLIENT_FILE'] = str(root / 'youtube-client.local.json')
    if not os.getenv('AUTOMATION_FRONTEND_URL'):
        os.environ['AUTOMATION_FRONTEND_URL'] = origin
    from app.main import app

    dist = root.parent / 'pwa' / 'dist'
    if not (dist / 'index.html').is_file():
        raise SystemExit('Build the phone interface with npm run build in pwa first.')
    print(f'On this PC: {origin}/studio', flush=True)
    for address in sorted(set(socket.gethostbyname_ex(socket.gethostname())[2])):
        if not address.startswith('127.'):
            print(f'On matching local network: http://{address}:{port}/studio', flush=True)
    uvicorn.run(with_phone_ui(app, dist), host='0.0.0.0', port=port, access_log=False)
