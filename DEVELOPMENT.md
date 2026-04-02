# 🛠️ Development Setup Guide

Instructions for setting up your local development environment.

## Prerequisites

- Python 3.8 or higher
- Git
- Spotify Developer Account (free)
- Code Editor (VS Code recommended)

## 1️⃣ Initial Setup

### Clone the repository

```bash
git clone https://github.com/your-username/lyrics-injector.git
cd lyrics-injector
```

### Create a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Install dependencies

```bash
pip install -r requirements.txt
```

## 2️⃣ Spotify API Configuration

1. Visit the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create a new application named "Lyrics Injector Dev".
3. Copy your `Client ID` and `Client Secret`.
4. Create your local `.env` file:
   ```bash
   cp .env.example .env
   ```
5. Add your credentials to the `.env` file.

## 3️⃣ Running the App

### Development Mode (with auto-reload)

```bash
python app.py
```

Visit: **http://localhost:8895**

## 4️⃣ Project Structure

```
lyrics-injector/
├── app.py                    # FastAPI Application & Endpoints
├── requirements.txt          # Python Dependencies
├── .env.example             # Environment template
├── .env                      # Your private credentials (not in git)
├── .gitignore               # Git exclusion list
├── README.md                # Documentation
├── CHANGELOG.md             # Change history
├── lyrics_data.db           # SQLite Database (auto-created)
├── docs/                    # Extra documentation
└── tests/                   # Testing suite
```

## 5️⃣ Codebase Overview

### `MetadataHandler` - Universal Reader/Writer

```python
# Reading metadata
meta = MetadataHandler.read_metadata("/path/to/file.mp3")
# Returns: {"artist": "...", "title": "..."}

# Injecting metadata
MetadataHandler.inject_metadata(
    file_path="/path/to/file.mp3",
    lyrics="...",
    cover_data=b"..."
)
```

### `LyricsEngine` - The Sync Core

```python
# Running the synchronization
await engine.run(
    music_path="D:/Music",
    mode="full",          # full, lyrics, covers
    retry_errors=False,
    force_refresh=False
)
```

### Adding Support for New Formats

1. Register the extension in `MetadataHandler.SUPPORTED_FORMATS`.
2. Create an injection method (e.g., `_inject_xyz`).
3. Update the main `inject_metadata()` switch logic.

## 6️⃣ Testing & Validation

### Small Scale Testing

We recommend testing with a small folder (5-10 songs) first:

1. Create a `test_music` folder.
2. Run the sync pointing to that path.
3. Verify the tags using a tool like Mp3tag or specialized scripts.

## 7️⃣ Debugging

Logs are output directly to the console:

```
DEBUG: Spotify token successfully refreshed.
DEBUG: Spotify cover found and downloaded for...
DEBUG: No LRCLib lyrics found for...
Error injecting in /path/to/file.mp3: ...
```

## 8️⃣ Git Workflow

### Before Committing

1. **Verify .env**: Ensure your credentials are NOT staged for commit.
2. **Commit Messages**: We follow conventional commits:
   - `feat:` New features.
   - `fix:` Bug fixes.
   - `docs:` Documentation.
   - `refactor:` Code improvements.

Example: `git commit -m "feat: add smart metadata detection"`

## 9️⃣ Common Issues

### "ModuleNotFoundError"

Ensure your virtual environment is active (`venv\Scripts\activate` or `source venv/bin/activate`).

### "Permission Denied" on database

This usually happens if another process (like a DB browser) is locking `lyrics_data.db`.

## 🔟 Environment Variables

```env
# Required
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...

# Optional
# LOG_LEVEL=INFO
# SERVER_PORT=8895
# DB_PATH=lyrics_data.db
```

---

**Happy coding! 🎵**
