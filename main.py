import io
from base64 import b64encode
from mutagen import File
from mutagen.mp4 import MP4, MP4Cover
from mutagen.apev2 import APEv2
from mutagen.wavpack import WavPack
from mutagen.wave import WAVE
from mutagen.oggopus import OggOpus
from mutagen.oggflac import OggFLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, USLT, APIC, TIT2, TPE1
from mutagen.easyid3 import EasyID3
import os
import asyncio
import aiohttp
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import base64
from dotenv import load_dotenv

load_dotenv()

# ===== SPOTIFY API CONFIG =====
# PREENCHA COM SUAS PRÓPRIAS CREDENCIAIS DO SPOTIFY DEVELOPER
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"

# ===== MUTAGEN SUPPORT FOR MULTIPLE FORMATS =====

# ------- DATABASE CONFIG -------
DB_PATH = os.getenv('DB_PATH')


def init_db():
    """Initialize database with tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS history 
                      (file_path TEXT PRIMARY KEY, 
                       artist TEXT, 
                       title TEXT, 
                       format TEXT,
                       status TEXT, 
                       last_attempt TEXT, 
                       error_msg TEXT, 
                       lyrics TEXT, 
                       lyrics_injected INTEGER DEFAULT 0,
                       cover_injected INTEGER DEFAULT 0,
                       cover_url TEXT,
                       retry_count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

# ------- METADATA HANDLER -------\


class MetadataHandler:
    """Unified handler for 11+ audio formats"""

    SUPPORTED_FORMATS = {
        '.mp3': 'MP3',
        '.flac': 'FLAC',
        '.m4a': 'M4A/AAC',
        '.aac': 'AAC',
        '.wav': 'WAV',
        '.ogg': 'OGG Vorbis',
        '.opus': 'Opus',
        '.wma': 'WMA',
        '.ape': 'APE',
        '.wv': 'WavPack',
        '.mka': 'Matroska Audio'
    }

    @staticmethod
    def read_metadata(file_path: str) -> Dict[str, str]:
        """Read metadata universally"""
        try:
            meta = File(file_path)
            if not meta:
                return None

            artist = None
            title = None

            for key in ['artist', 'TPE1', '\xa9ART', 'ARTIST']:
                if key in meta:
                    val = meta[key]
                    artist = val[0] if isinstance(val, list) else str(val)
                    if artist:
                        break

            for key in ['title', 'TIT2', '\xa9nam', 'TITLE']:
                if key in meta:
                    val = meta[key]
                    title = val[0] if isinstance(val, list) else str(val)
                    if title:
                        break

            return {'artist': artist or '', 'title': title or ''}
        except Exception as e:
            print(f"Error reading metadata from {file_path}: {e}")
            return None

    @staticmethod
    def get_format(extension: str) -> str:
        return MetadataHandler.SUPPORTED_FORMATS.get(extension.lower(), 'Desconhecido')

    @staticmethod
    def inject_metadata(file_path: str, lyrics: Optional[str] = None, cover_data: Optional[bytes] = None) -> bool:
        """Injeta metadados com suporte completo para Navidrome"""
        try:
            ext = Path(file_path).suffix.lower()

            if ext == '.mp3':
                try:
                    audio = ID3(file_path)
                except:
                    audio = ID3()
                if lyrics:
                    audio.setall(
                        "USLT", [USLT(encoding=3, lang='eng', desc='lyrics', text=lyrics)])
                if cover_data:
                    audio.setall("APIC", [
                                 APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data)])
                audio.save(file_path, v2_version=4)
                return True

            elif ext == '.flac':
                audio = FLAC(file_path)
                if lyrics:
                    audio["lyrics"] = lyrics
                if cover_data:
                    pic = Picture()
                    pic.data = cover_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    audio.clear_pictures()
                    audio.add_picture(pic)
                audio.save()
                return True

            elif ext in ['.m4a', '.aac']:
                audio = MP4(file_path)
                if lyrics:
                    audio['\xa9lyr'] = lyrics
                if cover_data:
                    audio['covr'] = [
                        MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
                return True

            elif ext in ['.ogg', '.opus']:
                if ext == '.ogg':
                    audio = OggVorbis(file_path)
                else:
                    audio = OggOpus(file_path)
                if lyrics:
                    audio['LYRICS'] = lyrics
                if cover_data:
                    pic = Picture()
                    pic.data = cover_data
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    audio["metadata_block_picture"] = [
                        b64encode(pic.write()).decode('ascii')]
                audio.save()
                return True

            elif ext == '.wav':
                audio = WAVE(file_path)
                if not audio.tags:
                    audio.add_tags()
                if lyrics:
                    audio.tags.setall(
                        "USLT", [USLT(encoding=3, lang='eng', desc='lyrics', text=lyrics)])
                if cover_data:
                    audio.tags.setall("APIC", [APIC(
                        encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data)])
                audio.save()
                return True

            elif ext in ['.wv', '.ape']:
                if ext == '.wv':
                    audio = WavPack(file_path)
                else:
                    audio = APEv2(file_path)
                if lyrics:
                    audio['lyrics'] = lyrics
                if cover_data:
                    pic = Picture()
                    pic.data = cover_data
                    pic.type = 3  # Front Cover
                    pic.mime = "image/jpeg"  # Assumindo JPEG
                    audio['Cover Art'] = pic
                audio.save()
                return True

            return False
        except Exception as e:
            print(f"Error injecting metadata into {file_path}: {e}")
            return False


# ------- LYRICS ENGINE -------\
class LyricsEngine:
    def __init__(self):
        self.is_running = False
        self.progress = {
            "current": 0,
            "total": 0,
            "status": "Idle",
            "success_lyrics": 0,
            "success_covers": 0,
            "total_injected": 0,
            "errors": 0,
            "skipped": 0
        }
        self._spotify_access_token = None
        self._spotify_token_expiry = datetime.now()

    def check_file_metadata(self, file_path: str, conn: sqlite3.Connection) -> Dict[str, bool]:
        """Check what metadata already exists for a file (both in file and database)"""
        file_path_str = str(file_path)

        # Check database
        cursor = conn.execute('''SELECT lyrics_injected, cover_injected, status 
                               FROM history WHERE file_path = ?''', (file_path_str,))
        db_row = cursor.fetchone()

        has_lyrics = False
        has_cover = False

        if db_row:
            db_lyrics, db_cover, db_status = db_row
            has_lyrics = bool(db_lyrics)
            has_cover = bool(db_cover)

        return {
            "has_lyrics": has_lyrics,
            "has_cover": has_cover,
            "in_database": db_row is not None
        }

    async def _get_spotify_access_token(self, session: aiohttp.ClientSession):
        if self._spotify_access_token and self._spotify_token_expiry > datetime.now():
            return self._spotify_access_token

        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            print("ERROR: SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not configured.")
            return None

        auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        encoded_auth_string = base64.b64encode(auth_string.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_auth_string}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = "grant_type=client_credentials"

        try:
            async with session.post(SPOTIFY_TOKEN_URL, headers=headers, data=data, timeout=10) as response:
                if response.status == 200:
                    token_info = await response.json()
                    self._spotify_access_token = token_info.get("access_token")
                    expires_in = token_info.get(
                        "expires_in", 3600)  # Default 1 hour
                    self._spotify_token_expiry = datetime.now() + timedelta(seconds=expires_in -
                                                                            60)  # Refresh 1 min before expiry
                    print("DEBUG: Token Spotify obtido/atualizado com sucesso.")
                    return self._spotify_access_token
                else:
                    print(f"ERROR: Failed to get Spotify token, status: {response.status}, response: {await response.text()}")
        except aiohttp.ClientError as e:
            print(f"ERROR: Network error getting Spotify token: {e}")
        except Exception as e:
            print(f"ERROR: Unexpected error getting Spotify token: {e}")
        return None

    # FIX: New function to fetch covers using Spotify API
    async def get_spotify_cover(self, session: aiohttp.ClientSession, artist: str, title: str) -> Optional[Dict[str, bytes]]:
        """Busca capa usando a API do Spotify"""
        cover_data = None
        cover_url = None

        access_token = await self._get_spotify_access_token(session)
        if not access_token:
            print("DEBUG: Não foi possível obter token de acesso do Spotify.")
            return None

        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        search_query = f"artist:{artist} track:{title}"
        params = {
            "q": search_query,
            "type": "track,album",  # Busca por faixas e álbuns
            "limit": 1
        }

        try:
            async with session.get(SPOTIFY_SEARCH_URL, headers=headers, params=params, timeout=10) as spotify_response:
                if spotify_response.status == 200:
                    spotify_data = await spotify_response.json()

                    # Prefer album cover if available in track, otherwise try album result
                    cover_info = None
                    if spotify_data.get('tracks') and spotify_data['tracks']['items']:
                        track_album = spotify_data['tracks']['items'][0].get(
                            'album')
                        if track_album and track_album.get('images'):
                            cover_info = track_album['images']
                    elif spotify_data.get('albums') and spotify_data['albums']['items']:
                        album = spotify_data['albums']['items'][0]
                        if album.get('images'):
                            cover_info = album['images']

                    if cover_info:
                        # Pega a capa de maior resolução (geralmente a primeira da lista)
                        if cover_info:
                            # Pega a primeira imagem, que geralmente é a maior
                            cover_url = cover_info[0]['url']

                            async with session.get(cover_url, timeout=10) as img_resp:
                                if img_resp.status == 200:
                                    cover_data = await img_resp.read()
                                    print(
                                        f"DEBUG: Capa do Spotify encontrada e baixada para {artist} - {title}.")
                                    return {'cover_data': cover_data, 'cover_url': cover_url}
                                else:
                                    print(
                                        f"DEBUG: Failed to download Spotify cover image, status {img_resp.status} from {cover_url}")
                    else:
                        print(
                            f"DEBUG: No Spotify cover found for {artist} - {title} in search.")
                else:
                    print(f"DEBUG: Spotify API error (status {spotify_response.status}) for {artist} - {title}, response: {await spotify_response.text()}")
        except aiohttp.ClientError as e:
            print(
                f"DEBUG: Network error while fetching Spotify cover for {artist} - {title}: {e}")
        except json.JSONDecodeError:
            print(
                f"DEBUG: JSON decode error from Spotify API for {artist} - {title}.")
        except Exception as e:
            print(
                f"ERROR: Unexpected error while fetching Spotify cover for {artist} - {title}: {e}")
        return None

    async def get_lyrics_lrclib(self, session, artist: str, title: str) -> Optional[str]:
        """Busca letras do LRClib"""
        try:
            async with session.get("https://lrclib.net/api/get",
                                   params={'artist_name': artist,
                                           'track_name': title},
                                   timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('syncedLyrics') or data.get('plainLyrics')
                else:
                    print(
                        f"DEBUG: No LRCLib lyrics found for {artist} - {title}, status: {resp.status}")
        except aiohttp.ClientError as e:
            print(
                f"DEBUG: Network error while fetching LRCLib lyrics for {artist} - {title}: {e}")
        except json.JSONDecodeError:
            print(
                f"DEBUG: JSON decode error from LRCLib API for {artist} - {title}.")
        except Exception as e:
            print(
                f"DEBUG: Unexpected error while fetching LRCLib lyrics for {artist} - {title}: {e}")
        return None

    async def run(self, music_path: str, mode: str = "full", retry_errors: bool = False, force_refresh: bool = False):
        """Main sync engine with smart metadata checking"""
        if not music_path:
            self.progress["status"] = "ERROR: Empty path"
            return

        self.is_running = True
        clean_path = os.path.abspath(music_path.replace("/", os.sep))
        p = Path(clean_path)

        self.progress = {
            "current": 0,
            "total": 0,
            "status": f"Scanning: {clean_path}",
            "success_lyrics": 0,
            "success_covers": 0,
            "total_injected": 0,
            "errors": 0,
            "skipped": 0
        }

        if not p.exists():
            self.progress["status"] = f"Error: Path {p} does not exist"
            self.is_running = False
            return

        files = []
        for ext in MetadataHandler.SUPPORTED_FORMATS.keys():
            files.extend(list(p.rglob(f'*{ext}')))

        if retry_errors:
            conn = sqlite3.connect(DB_PATH)
            error_files = [row[0] for row in conn.execute(
                "SELECT file_path FROM history WHERE status = 'ERROR'"
            ).fetchall()]
            conn.close()
            files = [f for f in files if str(f) in error_files]

        self.progress["total"] = len(files)
        if not files:
            self.progress["status"] = "No audio files found"
            self.is_running = False
            return

        conn = sqlite3.connect(DB_PATH)
        async with aiohttp.ClientSession() as session:
            for i, f_path in enumerate(files):
                if not self.is_running:
                    break

                self.progress["current"] = i + 1

                try:
                    meta = MetadataHandler.read_metadata(str(f_path))
                    if not meta or not meta['artist'] or not meta['title']:
                        conn.execute('''INSERT INTO history
                                      (file_path, artist, title, format, status, last_attempt, error_msg, retry_count)
                                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                      ON CONFLICT(file_path) DO UPDATE SET
                                      status='FAILED', error_msg=excluded.error_msg, last_attempt=excluded.last_attempt, retry_count=history.retry_count+1
                                   ''',
                                     (str(f_path), meta.get('artist', '') if meta else 'Unknown', f_path.name,
                                      MetadataHandler.get_format(
                                         f_path.suffix), "FAILED", datetime.now().isoformat(),
                                         "Metadados essenciais (artista/título) não encontrados.", 1))
                        conn.commit()
                        continue

                    artist = meta['artist']
                    title = meta['title']
                    ext = f_path.suffix.lower()
                    format_name = MetadataHandler.SUPPORTED_FORMATS.get(
                        ext, 'Unknown')

                    # ===== SMART METADATA CHECKING =====
                    existing_metadata = self.check_file_metadata(
                        str(f_path), conn)

                    # Determine what needs to be fetched
                    needs_lyrics = (mode in ["full", "lyrics"]) and (
                        force_refresh or not existing_metadata["has_lyrics"])
                    needs_cover = (mode in ["full", "covers"]) and (
                        force_refresh or not existing_metadata["has_cover"])

                    # Skip if all needed metadata already exists
                    if not needs_lyrics and not needs_cover:
                        self.progress["skipped"] += 1
                        self.progress["status"] = f"[SKIP] {artist} - {title}"
                        continue

                    self.progress["status"] = f"[{format_name}] {artist} - {title}" + (
                        " (Refreshing)" if force_refresh else "")

                    lyrics_injected = existing_metadata["has_lyrics"]
                    cover_injected = existing_metadata["has_cover"]

                    fetched_lyrics = None
                    fetched_cover_data = None
                    fetched_cover_url = None

                    # Only fetch what's missing
                    if needs_lyrics:
                        fetched_lyrics = await self.get_lyrics_lrclib(session, artist, title)
                        if fetched_lyrics:
                            self.progress["success_lyrics"] += 1
                            lyrics_injected = 1

                    if needs_cover:
                        cover_result = await self.get_spotify_cover(session, artist, title)
                        if cover_result:
                            fetched_cover_data = cover_result['cover_data']
                            fetched_cover_url = cover_result['cover_url']
                            self.progress["success_covers"] += 1
                            cover_injected = 1

                    # Update cover URL if we already had it
                    if existing_metadata["in_database"] and not fetched_cover_url:
                        cursor = conn.execute(
                            "SELECT cover_url FROM history WHERE file_path = ?", (str(f_path),))
                        row = cursor.fetchone()
                        if row:
                            fetched_cover_url = row[0]

                    db_status = "SUCCESS"
                    db_error_msg = ""

                    if fetched_lyrics or fetched_cover_data:
                        if MetadataHandler.inject_metadata(str(f_path), fetched_lyrics, fetched_cover_data):
                            self.progress["total_injected"] += 1
                        else:
                            db_status = "FAILED"
                            db_error_msg = "Failed to inject metadata into file."
                    elif existing_metadata["in_database"]:
                        # Already has metadata, don't change status
                        db_status = "SUCCESS"
                    else:
                        db_status = "FAILED"
                        db_error_msg = "No lyrics or cover found to inject."

                    conn.execute('''INSERT INTO history 
                                  (file_path, artist, title, format, status, last_attempt, 
                                   lyrics_injected, cover_injected, cover_url, retry_count, error_msg) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) 
                                  ON CONFLICT(file_path) DO UPDATE SET 
                                  lyrics_injected=MAX(lyrics_injected, excluded.lyrics_injected),
                                  cover_injected=MAX(cover_injected, excluded.cover_injected),
                                  cover_url=CASE WHEN excluded.cover_url != '' THEN excluded.cover_url ELSE cover_url END,
                                  last_attempt=excluded.last_attempt,
                                  status=excluded.status,
                                  error_msg=excluded.error_msg
                               ''',
                                 (str(f_path), artist, title, format_name, db_status,
                                  datetime.now().isoformat(), lyrics_injected, cover_injected,
                                  fetched_cover_url or "", 0, db_error_msg))

                except Exception as e:
                    self.progress["errors"] += 1
                    conn.execute('''INSERT INTO history 
                                  (file_path, artist, title, status, last_attempt, error_msg, retry_count) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?)
                                  ON CONFLICT(file_path) DO UPDATE SET
                                  status='ERROR',
                                  error_msg=excluded.error_msg,
                                  last_attempt=excluded.last_attempt,
                                  retry_count=retry_count + 1 
                               ''',
                                 (str(f_path), "Error", f_path.name, "ERROR",
                                  datetime.now().isoformat(), str(e), 1))

                conn.commit()
                await asyncio.sleep(0.01)

        conn.close()
        self.is_running = False
        self.progress["status"] = "✅ Completed"


# ------- FASTAPI APP -------\
app = FastAPI()
engine = LyricsEngine()
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SyncVibes - Library Automated Metadata</title>
        <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            html, body { width: 100%; height: 100%; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #121212;
                color: #fff;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            
            .sidebar {
                width: 300px;
                background: #000;
                border-right: 1px solid #282828;
                padding: 16px;
                overflow-y: auto;
                flex-shrink: 0;
            }
            
            .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
            
            .header {
                background: #181818;
                border-bottom: 1px solid #282828;
                padding: 16px 24px;
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 20px;
            }
            
            .header-title { font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
            
            .header-controls {
                display: flex;
                gap: 12px;
                align-items: center;
                justify-content: flex-end;
                flex: 1;
                max-width: 800px;
            }
            
            .path-input {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 24px;
                padding: 8px 16px;
                color: #fff;
                font-size: 13px;
                flex: 1;
                max-width: 400px;
                transition: all 0.2s;
            }
            
            .path-input:focus {
                outline: none;
                border-color: #1db954;
                background: #333;
            }
            
            .btn {
                background: #1db954;
                color: #000;
                border: none;
                border-radius: 24px;
                padding: 8px 24px;
                font-size: 12px;
                font-weight: 700;
                cursor: pointer;
                transition: all 0.2s;
                white-space: nowrap;
            }
            
            .btn:hover {
                background: #1ed760;
                transform: scale(1.05);
            }
            
            .btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
                transform: none;
            }
            
            .btn-secondary {
                background: transparent;
                color: #1db954;
                border: 1px solid #1db954;
            }
            
            .btn-secondary:hover {
                background: rgba(29, 185, 84, 0.1);
            }
            
            .content {
                flex: 1;
                display: flex;
                overflow: hidden;
            }
            
            .library-view {
                flex: 1;
                padding: 24px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
            }
            
            .library-title { font-size: 28px; font-weight: 700; margin-bottom: 24px; }
            
            .library-stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 12px;
                margin-bottom: 24px;
            }
            
            .stat-card {
                background: #282828;
                border-radius: 8px;
                padding: 16px;
            }
            
            .stat-value {
                font-size: 24px;
                font-weight: 700;
                color: #1db954;
            }
            
            .stat-label {
                font-size: 11px;
                color: #b3b3b3;
                margin-top: 8px;
                text-transform: uppercase;
            }
            
            .library-tabs {
                display: flex;
                gap: 0;
                border-bottom: 1px solid #282828;
                margin-bottom: 20px;
            }
            
            .tab {
                padding: 12px 0;
                border: none;
                background: transparent;
                color: #b3b3b3;
                cursor: pointer;
                font-size: 14px;
                font-weight: 500;
                margin-right: 24px;
                position: relative;
            }
            
            .tab.active {
                color: #1db954;
            }
            
            .tab.active::after {
                content: '';
                position: absolute;
                bottom: -1px;
                left: 0;
                right: 0;
                height: 2px;
                background: #1db954;
            }
            
            .tracks-grid {
                flex: 1;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 2px;
            }
            
            .track-row {
                display: grid;
                grid-template-columns: 50px 1fr 120px auto;
                gap: 16px;
                align-items: center;
                padding: 8px 12px;
                border-radius: 4px;
                background: transparent;
                cursor: pointer;
                transition: background 0.2s;
            }
            
            .track-row:hover { background: rgba(29, 185, 84, 0.1); }
            
            .track-art {
                width: 50px;
                height: 50px;
                border-radius: 4px;
                object-fit: cover;
                background: #282828;
            }
            
            .track-info h3 {
                font-size: 14px;
                font-weight: 500;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            
            .track-info p {
                font-size: 12px;
                color: #b3b3b3;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                margin-top: 4px;
            }
            
            .track-status { font-size: 11px; color: #b3b3b3; }
            
            .badge {
                display: inline-block;
                padding: 2px 8px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                margin-right: 4px;
            }
            
            .badge-success {
                background: rgba(29, 185, 84, 0.2);
                color: #1db954;
            }
            
            .badge-error {
                background: rgba(239, 68, 68, 0.2);
                color: #f87171;
            }
            
            .sidebar-section { margin-bottom: 32px; }
            
            .sidebar-title {
                font-size: 11px;
                font-weight: 700;
                color: #b3b3b3;
                text-transform: uppercase;
                margin-bottom: 12px;
                letter-spacing: 1.5px;
            }
            
            .sidebar-item {
                padding: 12px;
                border-radius: 6px;
                background: transparent;
                border: none;
                color: #b3b3b3;
                cursor: pointer;
                text-align: left;
                font-size: 14px;
                transition: all 0.2s;
                width: 100%;
            }
            
            .sidebar-item:hover {
                color: #1db954;
                background: #282828;
            }
            
            .status-bar {
                background: #282828;
                border-top: 1px solid #404040;
                padding: 12px 24px;
                font-size: 12px;
                color: #b3b3b3;
            }
            
            .progress-bar {
                width: 100%;
                height: 3px;
                background: #404040;
                border-radius: 2px;
                overflow: hidden;
                margin-top: 8px;
            }
            
            .progress-fill {
                height: 100%;
                background: #1db954;
                transition: width 0.3s;
            }
            
            ::-webkit-scrollbar { width: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb {
                background: #404040;
                border-radius: 4px;
            }
            ::-webkit-scrollbar-thumb:hover { background: #535353; }
        </style>
    </head>
    <body>
        <div x-data="{
            tab: 'all',
            musicPath: 'D:/Music',
            history: [],
            stats: {},
            isRunning: false
        }" 
        x-init="setInterval(() => {
            fetch('/status').then(r => r.json()).then(d => {
                stats = d;
                isRunning = d.is_running;
            });
            fetch('/history').then(r => r.json()).then(d => history = d);
        }, 500)"
        style="width: 100%; height: 100%; display: flex; flex-direction: column;">
            
            <div class="header">
                <div class="header-title">🎵 SyncVibes - Library Automated Metadata</div>
                <div class="header-controls">
                    <input type="text" x-model="musicPath" placeholder="Library path..." class="path-input">
                    <button @click="!isRunning && fetch('/sync?path=' + encodeURIComponent(musicPath) + '&mode=full')" :disabled="isRunning" class="btn">Full Sync</button>
                    <button @click="!isRunning && fetch('/sync?path=' + encodeURIComponent(musicPath) + '&mode=lyrics')" :disabled="isRunning" class="btn btn-secondary">Lyrics</button>
                    <button @click="!isRunning && fetch('/sync?path=' + encodeURIComponent(musicPath) + '&mode=covers')" :disabled="isRunning" class="btn btn-secondary">Covers</button>
                    <button @click="!isRunning && fetch('/sync?path=' + encodeURIComponent(musicPath) + '&mode=full&force_refresh=true')" :disabled="isRunning" class="btn btn-secondary">🔄 Refresh All</button>
                    <button @click="!isRunning && history.some(h => h.status == 'ERROR') && fetch('/sync?path=' + encodeURIComponent(musicPath) + '&mode=full&retry_errors=true')" :disabled="isRunning || !history.some(h => h.status == 'ERROR')" class="btn btn-secondary">Retry Errors</button>
                </div>
            </div>
            
            <div class="content">
                <div class="sidebar">
                <div class="sidebar-section">
                        <div class="sidebar-title">Status</div>
                        <div style="font-size: 12px; color: #b3b3b3;">
                            <span x-show="isRunning" style="color: #1db954; margin-right: 6px;">●</span>
                            <span x-text="stats.status || 'Waiting...'"></span>
                        </div>
                        <div class="progress-bar" style="margin-top: 12px;">
                            <div class="progress-fill" :style="'width: ' + (stats.total > 0 ? (stats.current/stats.total)*100 : 0) + '%'"></div>
                        </div>
                    </div>
                    <div class="sidebar-section">
                        <div class="sidebar-title">Stats</div>
                        <div style="display: grid; grid-template-columns: 1fr; gap: 8px;">
                            <div class="stat-card">
                                <div class="stat-value" x-text="(stats.total > 0 ? Math.round((stats.current/stats.total)*100) : 0) + '%'"></div>
                                <div class="stat-label">Progress</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-value" x-text="stats.success_lyrics || 0"></div>
                                <div class="stat-label">Lyrics</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-value" x-text="stats.success_covers || 0"></div>
                                <div class="stat-label">Covers</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-value" x-text="stats.total_injected || 0"></div>
                                <div class="stat-label">Injected</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-value" x-text="stats.skipped || 0" style="color: #999;"></div>
                                <div class="stat-label">Skipped</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-value" x-text="stats.errors || 0" style="color: #f87171;"></div>
                                <div class="stat-label">Errors</div>
                            </div>
                        </div>
                    </div>
                    
                    
                </div>
                
                <div class="library-view">
                    <div class="library-title">Your Library</div>
                    
                    <div class="library-stats">
                        <div class="stat-card">
                            <div class="stat-value" x-text="history.length"></div>
                            <div class="stat-label">Total Tracks</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" x-text="history.filter(h => h.status == 'SUCCESS').length"></div>
                            <div class="stat-label">Synced</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" x-text="history.filter(h => h.status == 'ERROR').length"></div>
                            <div class="stat-label">Errored</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" x-text="history.filter(h => h.lyrics_injected).length"></div>
                            <div class="stat-label">With Lyrics</div>
                        </div>
                    </div>
                    
                    <div class="library-tabs">
                        <button @click="tab = 'all'" :class="tab == 'all' ? 'active' : ''" class="tab">All</button>
                        <button @click="tab = 'synced'" :class="tab == 'synced' ? 'active' : ''" class="tab">Synced</button>
                        <button @click="tab = 'errors'" :class="tab == 'errors' ? 'active' : ''" class="tab">Errors</button>
                    </div>
                    
                    <div class="tracks-grid">
                        <template x-for="track in history.filter(t => tab == 'all' ? true : (tab == 'synced' ? t.status == 'SUCCESS' : t.status == 'ERROR'))">
                            <div class="track-row">
                                <div>
                                    <template x-if="track.cover_url && track.cover_url.startsWith('http')">
                                        <img :src="track.cover_url" class="track-art" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22%3E%3Crect fill=%22%23282828%22 width=%22100%22 height=%22100%22/%3E%3C/svg%3E'">
                                    </template>
                                    <template x-if="!track.cover_url || !track.cover_url.startsWith('http')">
                                        <div class="track-art"></div>
                                    </template>
                                </div>
                                <div class="track-info">
                                    <h3 x-text="track.title || 'Unknown'"></h3>
                                    <p x-text="track.artist || 'Unknown Artist'"></p>
                                </div>
                                <div class="track-status">
                                    <template x-if="track.status == 'SUCCESS'">
                                        <div>
                                            <span x-show="track.lyrics_injected" class="badge badge-success">LRC</span>
                                            <span x-show="track.cover_injected" class="badge badge-success">ART</span>
                                        </div>
                                    </template>
                                    <template x-if="track.status == 'ERROR'">
                                        <span class="badge badge-error">ERROR</span>
                                    </template>
                                </div>
                                <div style="text-align: right; font-size: 11px; color: #b3b3b3;">
                                    <span x-text="track.format || '-'"></span>
                                </div>
                            </div>
                        </template>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


@app.get("/sync")
async def start_sync(path: str, mode: str = "full", retry_errors: bool = False, force_refresh: bool = False, background_tasks: BackgroundTasks = None):
    """Start synchronization"""
    background_tasks.add_task(engine.run, path, mode,
                              retry_errors, force_refresh)
    return {"message": f"Sync started in mode {mode}" + (" (Force Refresh)" if force_refresh else "")}


@app.get("/status")
async def get_status():
    """Get current status"""
    return {**engine.progress, "is_running": engine.is_running}


@app.get("/history")
async def get_history():
    """Get history"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    res = [dict(r) for r in conn.execute(
        "SELECT * FROM history ORDER BY last_attempt DESC"
    ).fetchall()]
    conn.close()
    return res


@app.get("/supported-formats")
async def get_formats():
    """Get supported formats"""
    return MetadataHandler.SUPPORTED_FORMATS


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8895)
