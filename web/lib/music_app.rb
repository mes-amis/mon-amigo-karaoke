require "open3"
require "pathname"

module KaraokeWeb
  # Bridge to the macOS Music.app via AppleScript. Two responsibilities:
  #   - `find_local(title:, artist:)` → on-disk path of the matching track
  #     if it's already downloaded, else nil.
  #   - `trigger_download(title:, artist:)` → issues `download t` on a
  #     matching cloud-library track and returns true on success.
  #
  # The script runner is injectable so tests can replace osascript without
  # invoking the real Music.app. Returns nil on non-macOS hosts.
  module MusicApp
    # Canonical macOS music folders. Both the legacy iTunes layout and the
    # modern Music.app one are covered — users frequently have files in one
    # but not the other, or both after migrating. The Python code uses the
    # same three roots, so results stay consistent across Ruby↔Python paths.
    DEFAULT_FS_ROOTS = [
      File.expand_path("~/Music/iTunes/iTunes Media/Music"),
      File.expand_path("~/Music/Music/Media/Music"),
      File.expand_path("~/Music/Music/Media.localized/Music"),
    ].freeze

    FS_AUDIO_EXTS = %w[.mp3 .m4a .aac .wav .flac .aif .aiff].freeze
    FS_CACHE_TTL = 600 # seconds — invalidated on /admin/rescan too

    class << self
      # Swap this in tests: ->(script) { ... } returning stdout String or nil
      attr_accessor :script_runner
      # Test override — swap the filesystem roots to a tmp dir.
      attr_writer :fs_roots

      def available?
        RUBY_PLATFORM.include?("darwin")
      end

      def run(script)
        runner = script_runner || method(:default_osascript)
        runner.call(script)
      end

      def default_osascript(script)
        return nil unless available?
        out, _err, status = Open3.capture3("osascript", "-e", script)
        status.success? ? out.strip : nil
      rescue StandardError
        nil
      end

      def escape(str)
        str.to_s.gsub("\\", "\\\\\\\\").gsub('"', '\\"')
      end

      # Fuzzy-search Music.app's local library. Returns hashes of
      # {title, artist, album, path} for tracks whose name/artist/album
      # matches `query` AND that resolve to a real on-disk file.
      # Cloud-only items (no POSIX path) are filtered out.
      def search_library(query, limit: 20)
        q = query.to_s.strip
        return [] if q.empty?

        script = <<~APPLESCRIPT
          tell application "System Events" to set musicRunning to (exists process "Music")
          if not musicRunning then return ""
          set q to "#{escape(q)}"
          tell application "Music"
            set matched to (every track of library playlist 1 whose (name contains q) or (artist contains q) or (album contains q))
            set total to count of matched
            set lim to #{Integer(limit)}
            if total < lim then set lim to total
            set output to ""
            repeat with i from 1 to lim
              set t to item i of matched
              try
                set loc to POSIX path of (location of t as alias)
              on error
                set loc to ""
              end try
              if loc is not "" then
                set output to output & (name of t) & tab & (artist of t) & tab & (album of t) & tab & loc & linefeed
              end if
            end repeat
            return output
          end tell
        APPLESCRIPT

        raw = run(script)
        return [] if raw.nil? || raw.empty?
        raw.each_line.filter_map do |line|
          parts = line.rstrip.split("\t")
          next if parts.length < 4
          title, artist, album, path = parts
          next if title.to_s.strip.empty?
          next unless File.file?(path)
          {
            title: title.strip,
            artist: artist.strip,
            album: album.strip,
            path: path,
          }
        end
      end

      def fs_roots
        @fs_roots || DEFAULT_FS_ROOTS
      end

      # Walk the standard music folders for audio files matching `query`.
      # Catches tracks Music.app can't see — most commonly the legacy
      # `~/Music/iTunes/...` layout that wasn't re-imported after the
      # iTunes→Music.app migration. Path convention assumed:
      #   <root>/<Artist>/<Album>/<NN Title>.<ext>
      # The leading track-number prefix (e.g. "04 " or "01-02 ") is stripped.
      #
      # Index is cached in-memory for FS_CACHE_TTL seconds — for a typical
      # library this is under a megabyte and a fraction of a second to
      # build, so we refresh lazily. `invalidate_fs_index!` forces a rebuild
      # (/admin/rescan calls this).
      def search_filesystem(query, limit: 30)
        q = query.to_s.downcase.strip
        return [] if q.empty?
        index = fs_index
        matched = index.select { |e| e[:haystack].include?(q) }
        matched.first(limit).map do |e|
          { title: e[:title], artist: e[:artist], album: e[:album], path: e[:path] }
        end
      end

      def fs_index
        now = Time.now.to_f
        if @fs_index && @fs_indexed_at && (now - @fs_indexed_at) < FS_CACHE_TTL
          return @fs_index
        end
        @fs_index = build_fs_index
        @fs_indexed_at = now
        @fs_index
      end

      def invalidate_fs_index!
        @fs_index = nil
        @fs_indexed_at = nil
      end

      def build_fs_index
        entries = []
        seen_paths = {}
        fs_roots.each do |root|
          next unless File.directory?(root)
          root_pn = Pathname.new(root)
          Dir.glob(File.join(root, "**", "*")).each do |path|
            next if seen_paths[path]
            ext = File.extname(path).downcase
            next unless FS_AUDIO_EXTS.include?(ext)
            next unless File.file?(path)
            rel_parts = Pathname.new(path).relative_path_from(root_pn).to_s.split(File::SEPARATOR)

            artist = rel_parts.length >= 3 ? rel_parts[0] : ""
            album =
              if rel_parts.length >= 3
                rel_parts[1]
              elsif rel_parts.length == 2
                rel_parts[0]
              else
                ""
              end
            raw = File.basename(path, ext)
            title = raw.sub(/\A\d+[-\d]*\s+/, "").strip
            title = raw if title.empty?

            seen_paths[path] = true
            entries << {
              title: title,
              artist: artist,
              album: album,
              path: path,
              haystack: "#{title} #{album} #{artist}".downcase,
            }
          end
        end
        entries
      end

      def find_local(title:, artist:)
        title_s = title.to_s.strip
        artist_s = artist.to_s.strip
        return nil if title_s.empty? || artist_s.empty?

        script = <<~APPLESCRIPT
          tell application "System Events" to set musicRunning to (exists process "Music")
          if not musicRunning then return ""
          tell application "Music"
            set matched to (every track of library playlist 1 whose name is "#{escape(title_s)}" and artist is "#{escape(artist_s)}")
            if (count of matched) = 0 then return ""
            set t to first item of matched
            try
              return POSIX path of (location of t as alias)
            on error
              return ""
            end try
          end tell
        APPLESCRIPT

        out = run(script)
        return nil if out.nil? || out.empty?
        File.file?(out) ? out : nil
      end

      # Ask Music.app to download a cloud-library track (iTunes/Apple Music
      # tracks that are "in your library" but not yet downloaded locally).
      # Returns true if the download was successfully *triggered* — the file
      # itself may take time to appear; caller should poll find_local.
      def trigger_download(title:, artist:)
        title_s = title.to_s.strip
        artist_s = artist.to_s.strip
        return false if title_s.empty? || artist_s.empty?

        script = <<~APPLESCRIPT
          tell application "System Events" to set musicRunning to (exists process "Music")
          if not musicRunning then
            tell application "Music" to launch
          end if
          tell application "Music"
            set matched to (every track of library playlist 1 whose name is "#{escape(title_s)}" and artist is "#{escape(artist_s)}")
            if (count of matched) = 0 then return "missing"
            set t to first item of matched
            try
              download t
              return "ok"
            on error err
              return "error:" & err
            end try
          end tell
        APPLESCRIPT

        run(script) == "ok"
      end
    end
  end
end
