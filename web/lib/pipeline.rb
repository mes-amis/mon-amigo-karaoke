require "open3"
require "fileutils"

require_relative "db"
require_relative "library"
require_relative "music_app"

module KaraokeWeb
  # Background coordinator that turns a freshly-picked song into a renderable
  # MP4. For each song we kick off a single worker thread that walks this
  # state machine:
  #
  #   pending → downloading → rendering → ready
  #                                  └───→ failed
  #
  # Guarded against double-enqueue by an in-flight set. All DB writes go
  # through the module's own SQLite connection (per-thread).
  module Pipeline
    DOWNLOAD_POLL_INTERVAL = 3  # seconds between Music.app polls
    DOWNLOAD_TIMEOUT = 300      # give up after 5 min
    RENDER_TIMEOUT = 1200       # 20 min hard cap for one bin/karaoke run

    @in_flight = {}
    @lock = Mutex.new

    class << self
      # Swap in tests: ->(cmd, env) { [out, err, status] }
      attr_accessor :renderer
      # Swap in tests: ->(interval) { ... } — tests pass a no-op sleeper.
      attr_accessor :sleeper

      # Kick off the download+render flow for a song. Idempotent:
      # returns the running thread, or nil if already in flight / already ready.
      def enqueue(song_id, async: true)
        row = DB.connection.get_first_row(
          "SELECT id, status, video_path FROM songs WHERE id = ?", [song_id]
        )
        return nil unless row

        if row["status"] == "ready" && row["video_path"] && File.file?(row["video_path"])
          return nil
        end

        claimed = @lock.synchronize do
          next false if @in_flight[song_id]
          @in_flight[song_id] = true
          true
        end
        return nil unless claimed

        runner = -> { run(song_id) }
        if async
          Thread.new do
            begin
              runner.call
            rescue => e
              mark_failed(song_id, "pipeline crashed: #{e.class}: #{e.message}")
            ensure
              @lock.synchronize { @in_flight.delete(song_id) }
            end
          end
        else
          begin
            runner.call
          ensure
            @lock.synchronize { @in_flight.delete(song_id) }
          end
          nil
        end
      end

      def in_flight?(song_id)
        @lock.synchronize { !!@in_flight[song_id] }
      end

      def run(song_id)
        song = DB.connection.get_first_row("SELECT * FROM songs WHERE id = ?", [song_id])
        return unless song

        title = song["title"].to_s
        artist = song["artist"].to_s
        album = song["album"].to_s

        # Short-circuit: if the MP4 already exists under the canonical name,
        # we're done — just point the row at it.
        if Library.video_exists?(title)
          DB.upsert_song(
            title: title,
            video_path: Library.path_for(title),
            status: "ready",
          )
          return
        end

        # Step 1: find the audio file.
        # Preferred source: an explicit audio_path on the song row (set by
        # admin "Fix" action or by a participant picking a filesystem
        # fallback result). If missing, fall back to Music.app lookup.
        preset = song["audio_path"].to_s
        local =
          if !preset.empty? && File.file?(preset)
            preset
          else
            find_local_with_artist(title, artist)
          end

        if local.nil?
          # Cloud-library track — ask Music.app to download it, then poll.
          mark_status(song_id, "downloading")
          if artist.empty? || !MusicApp.trigger_download(title: title, artist: artist)
            mark_failed(song_id,
              "Music.app doesn't have \"#{title}\"#{artist.empty? ? "" : " by #{artist}"}. " \
              "Add it to your Apple Music library first, then hit Retry.")
            return
          end

          local = wait_for_download(title, artist)
          unless local
            mark_failed(song_id, "Music.app download timed out after #{DOWNLOAD_TIMEOUT / 60} minutes.")
            return
          end
        end

        # Step 2: render via the Python CLI.
        mark_status(song_id, "rendering")
        output = Library.path_for(title)
        FileUtils.mkdir_p(File.dirname(output))

        cmd = [karaoke_bin, local, "-o", output]
        cmd << "--artist" << artist unless artist.empty?
        cmd << "--album" << album unless album.empty?

        out, err, status = invoke_renderer(cmd)
        if status && status.respond_to?(:success?) && status.success? && File.file?(output)
          DB.upsert_song(title: title, video_path: output, status: "ready")
        else
          tail = (err.to_s.empty? ? out.to_s : err.to_s).lines.last(4).join.strip
          mark_failed(song_id, "render failed: #{tail}".slice(0, 500))
        end
      end

      def find_local_with_artist(title, artist)
        return nil if artist.nil? || artist.empty?
        MusicApp.find_local(title: title, artist: artist)
      end

      def wait_for_download(title, artist)
        start = Time.now
        sleeper_fn = sleeper || ->(s) { sleep(s) }
        loop do
          sleeper_fn.call(DOWNLOAD_POLL_INTERVAL)
          path = MusicApp.find_local(title: title, artist: artist)
          return path if path
          return nil if (Time.now - start) >= DOWNLOAD_TIMEOUT
        end
      end

      def invoke_renderer(cmd)
        if renderer
          renderer.call(cmd)
        else
          Open3.capture3(*cmd)
        end
      end

      def karaoke_bin
        # web/lib/pipeline.rb → web/lib → web → (repo root) → bin/karaoke
        File.expand_path("../../bin/karaoke", __dir__)
      end

      def mark_status(song_id, status)
        DB.connection.execute(
          "UPDATE songs SET status = ?, error = NULL, updated_at = datetime('now') WHERE id = ?",
          [status, song_id]
        )
      end

      def mark_failed(song_id, msg)
        DB.connection.execute(
          "UPDATE songs SET status = 'failed', error = ?, updated_at = datetime('now') WHERE id = ?",
          [msg, song_id]
        )
      end
    end
  end
end
