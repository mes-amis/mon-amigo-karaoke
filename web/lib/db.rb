require "sqlite3"
require "fileutils"

module KaraokeWeb
  module DB
    DEFAULT_PATH = File.expand_path("../data/session.db", __dir__)

    class << self
      attr_writer :path

      def path
        @path ||= ENV["KARAOKE_DB_PATH"] || DEFAULT_PATH
      end

      def connection
        Thread.current[:karaoke_db] ||= begin
          FileUtils.mkdir_p(File.dirname(path))
          db = SQLite3::Database.new(path)
          db.results_as_hash = true
          db.execute("PRAGMA foreign_keys = ON")
          db.execute("PRAGMA journal_mode = WAL")
          db
        end
      end

      def migrate!
        c = connection
        c.execute <<~SQL
          CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
          )
        SQL
        c.execute <<~SQL
          CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT,
            album TEXT,
            video_path TEXT,
            status TEXT NOT NULL DEFAULT 'ready',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
          )
        SQL
        c.execute <<~SQL
          CREATE UNIQUE INDEX IF NOT EXISTS songs_title_idx
            ON songs(title)
        SQL
        c.execute <<~SQL
          CREATE TABLE IF NOT EXISTS queue_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
            song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
          )
        SQL
        c.execute <<~SQL
          CREATE INDEX IF NOT EXISTS queue_state_idx
            ON queue_entries(state, position)
        SQL
      end

      def next_position
        row = connection.get_first_row(
          "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM queue_entries WHERE state IN ('pending','performing')"
        )
        row["next"] || 1
      end

      def upsert_song(title:, artist: nil, album: nil, video_path: nil, status: "ready")
        c = connection
        existing = c.get_first_row("SELECT * FROM songs WHERE title = ?", [title])
        if existing
          c.execute(
            "UPDATE songs SET artist = COALESCE(?, artist), album = COALESCE(?, album), " \
            "video_path = COALESCE(?, video_path), status = ?, updated_at = datetime('now') WHERE id = ?",
            [artist, album, video_path, status, existing["id"]]
          )
          existing["id"]
        else
          c.execute(
            "INSERT INTO songs(title, artist, album, video_path, status) VALUES (?, ?, ?, ?, ?)",
            [title, artist, album, video_path, status]
          )
          c.last_insert_row_id
        end
      end
    end
  end
end
