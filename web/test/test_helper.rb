ENV["RACK_ENV"] = "test"

require "minitest/autorun"
require "rack/test"
require "tmpdir"
require "fileutils"
require "json"

# Shared per-test isolation: each test gets its own SQLite file and its own
# fake "ready-videos" directory so nothing leaks between tests (and nothing
# touches the real ~/Desktop/mon-amigo-karaoke folder).
module KaraokeTestIsolation
  def setup
    super if defined?(super)
    @_tmp = Dir.mktmpdir("karaoke-test-")
    @db_path = File.join(@_tmp, "session.db")
    @video_root = File.join(@_tmp, "videos")
    FileUtils.mkdir_p(@video_root)

    # Cache the original env we're about to stomp on so teardown restores it.
    @_saved_env = {
      "KARAOKE_DB_PATH" => ENV["KARAOKE_DB_PATH"],
      "KARAOKE_VIDEO_DIR" => ENV["KARAOKE_VIDEO_DIR"],
      "KARAOKE_ADMIN_PASSWORD" => ENV["KARAOKE_ADMIN_PASSWORD"],
      "KARAOKE_SESSION_SECRET" => ENV["KARAOKE_SESSION_SECRET"],
    }
    ENV["KARAOKE_DB_PATH"] = @db_path
    ENV["KARAOKE_VIDEO_DIR"] = @video_root
    ENV["KARAOKE_SESSION_SECRET"] = "a" * 64

    require_relative "../lib/db"
    require_relative "../lib/library"
    # Reset module-level memo so env wins.
    KaraokeWeb::DB.instance_variable_set(:@path, nil)
    KaraokeWeb::DB.connection.close rescue nil
    Thread.current[:karaoke_db] = nil
    KaraokeWeb::Library.instance_variable_set(:@root, nil)
  end

  def teardown
    super if defined?(super)
    if Thread.current[:karaoke_db]
      Thread.current[:karaoke_db].close rescue nil
      Thread.current[:karaoke_db] = nil
    end
    @_saved_env&.each { |k, v| v.nil? ? ENV.delete(k) : ENV[k] = v }
    KaraokeWeb::DB.instance_variable_set(:@path, nil) if defined?(KaraokeWeb::DB)
    KaraokeWeb::Library.instance_variable_set(:@root, nil) if defined?(KaraokeWeb::Library)
    FileUtils.remove_entry(@_tmp) if @_tmp && Dir.exist?(@_tmp)
  end

  # Drops a zero-byte .mp4 with the given title into the fake library.
  def make_video(title)
    path = File.join(@video_root, "#{title}.mp4")
    FileUtils.touch(path)
    path
  end
end
