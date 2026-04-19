require_relative "test_helper"
require_relative "../app" # pulls in pipeline, db, library, music_app

class PipelineTest < Minitest::Test
  include KaraokeTestIsolation

  def setup
    super
    KaraokeWeb::DB.migrate!
    # Neuter the real Music.app / renderer for every test; individual tests
    # override these to script specific behavior.
    KaraokeWeb::MusicApp.script_runner = ->(_) { "" }
    KaraokeWeb::Pipeline.renderer = ->(_cmd) { ["", "", fake_status(true)] }
    KaraokeWeb::Pipeline.sleeper = ->(_) { } # no sleeping in tests
  end

  def teardown
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::Pipeline.renderer = nil
    KaraokeWeb::Pipeline.sleeper = nil
    super
  end

  def fake_status(ok)
    ok ? ok_status : bad_status
  end

  def ok_status
    Class.new { def success?; true; end }.new
  end

  def bad_status
    Class.new { def success?; false; end }.new
  end

  def song_status(id)
    KaraokeWeb::DB.connection.get_first_row(
      "SELECT status, error, video_path FROM songs WHERE id = ?", [id]
    )
  end

  # ---- short-circuit cases --------------------------------------------

  def test_short_circuits_when_video_already_exists
    make_video("Ocean Man")
    id = KaraokeWeb::DB.upsert_song(title: "Ocean Man", status: "pending")
    KaraokeWeb::Pipeline.renderer = ->(_cmd) { raise "renderer must not be called" }
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "ready", row["status"]
    assert_equal File.join(@video_root, "Ocean Man.mp4"), row["video_path"]
  end

  def test_noop_when_song_already_ready_and_file_present
    path = make_video("Doctor Worm")
    id = KaraokeWeb::DB.upsert_song(title: "Doctor Worm", video_path: path, status: "ready")
    called = 0
    KaraokeWeb::Pipeline.renderer = ->(_) { called += 1; ["", "", ok_status] }
    assert_nil KaraokeWeb::Pipeline.enqueue(id, async: false),
      "already-ready with file on disk should be a no-op"
    assert_equal 0, called
  end

  # ---- happy path -----------------------------------------------------

  def test_local_match_goes_straight_to_rendering_then_ready
    # Music.app reports the audio is on disk (and our renderer will "render"
    # successfully by creating the MP4 file).
    local_audio = File.join(@_tmp, "source.m4a")
    FileUtils.touch(local_audio)
    KaraokeWeb::MusicApp.script_runner = ->(_) { local_audio }

    output_video = File.join(@video_root, "Ocean Man.mp4")
    KaraokeWeb::Pipeline.renderer = ->(cmd) {
      assert_includes cmd, local_audio
      assert_includes cmd, "-o"
      assert_includes cmd, output_video
      assert_includes cmd, "--artist"
      assert_includes cmd, "Ween"
      FileUtils.touch(output_video)
      ["ok", "", ok_status]
    }

    id = KaraokeWeb::DB.upsert_song(
      title: "Ocean Man", artist: "Ween", album: "The Mollusk", status: "pending"
    )
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "ready", row["status"]
    assert_equal output_video, row["video_path"]
  end

  def test_cloud_track_triggers_download_then_polls_then_renders
    # Music.app script is called multiple times: find_local (empty), then
    # trigger_download ("ok"), then find_local again (returns the path).
    local_audio = File.join(@_tmp, "downloaded.m4a")
    script_responses = [
      "",            # initial find_local: not on disk
      "ok",          # trigger_download
      "",            # poll #1: still downloading
      local_audio,   # poll #2: file appeared (set up below)
    ]
    call_index = 0
    KaraokeWeb::MusicApp.script_runner = ->(_script) {
      r = script_responses[call_index]
      call_index += 1
      if r == local_audio
        FileUtils.touch(local_audio) # file "finished downloading"
      end
      r
    }

    output_video = File.join(@video_root, "Fight Test.mp4")
    KaraokeWeb::Pipeline.renderer = ->(_cmd) {
      FileUtils.touch(output_video)
      ["", "", ok_status]
    }

    id = KaraokeWeb::DB.upsert_song(
      title: "Fight Test", artist: "The Flaming Lips", status: "pending"
    )
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "ready", row["status"], row["error"].to_s
    assert_equal output_video, row["video_path"]
  end

  # ---- failure paths --------------------------------------------------

  def test_marks_failed_when_music_app_has_nothing
    # trigger_download returns "missing" (not "ok").
    KaraokeWeb::MusicApp.script_runner = ->(_script) { "missing" }

    id = KaraokeWeb::DB.upsert_song(
      title: "Obscure Song", artist: "Nobody", status: "pending"
    )
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "failed", row["status"]
    assert_match(/Music\.app doesn't have/i, row["error"])
  end

  def test_marks_failed_when_renderer_fails
    local_audio = File.join(@_tmp, "source.m4a")
    FileUtils.touch(local_audio)
    KaraokeWeb::MusicApp.script_runner = ->(_) { local_audio }
    KaraokeWeb::Pipeline.renderer = ->(_cmd) {
      ["", "ffmpeg exploded\ncould not process\n", bad_status]
    }

    id = KaraokeWeb::DB.upsert_song(
      title: "x", artist: "y", status: "pending"
    )
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "failed", row["status"]
    assert_match(/render failed/, row["error"])
  end

  def test_marks_failed_when_artist_missing
    # No artist → Music.app can't be queried; pipeline must fail fast
    # rather than spamming downloads blindly.
    id = KaraokeWeb::DB.upsert_song(title: "Mystery", status: "pending")
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    row = song_status(id)
    assert_equal "failed", row["status"]
  end

  # ---- concurrency guards --------------------------------------------

  def test_async_enqueue_returns_thread_and_is_idempotent
    local_audio = File.join(@_tmp, "source.m4a")
    FileUtils.touch(local_audio)
    KaraokeWeb::MusicApp.script_runner = ->(_) { local_audio }
    latch = Queue.new
    run_count = 0
    KaraokeWeb::Pipeline.renderer = ->(_cmd) {
      run_count += 1
      latch.pop # block the worker until we signal
      FileUtils.touch(File.join(@video_root, "Blocked.mp4"))
      ["", "", ok_status]
    }

    id = KaraokeWeb::DB.upsert_song(title: "Blocked", artist: "a", status: "pending")
    t1 = KaraokeWeb::Pipeline.enqueue(id)
    assert_kind_of Thread, t1
    # Second enqueue while first is still running should be a no-op.
    assert_nil KaraokeWeb::Pipeline.enqueue(id), "duplicate enqueue should be refused"

    latch << :go
    t1.join(5)
    assert_equal 1, run_count
    assert_equal "ready", song_status(id)["status"]
  end
end
