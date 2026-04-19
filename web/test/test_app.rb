require_relative "test_helper"

# Load the app AFTER the helper has a chance to set env, so the configure
# block picks up our test DB path and video root.
require_relative "../app"

class AppTest < Minitest::Test
  include Rack::Test::Methods
  include KaraokeTestIsolation

  def setup
    super
    # App was loaded once; re-run migrations + library sync against the
    # per-test DB so each test starts fresh.
    KaraokeWeb::DB.migrate!
    KaraokeWeb::App.sync_library_once!
    # Seed a few fake videos so there's something to pick.
    @video_a = make_video("Ocean Man")
    @video_b = make_video("Doctor Worm")
    KaraokeWeb::App.sync_library_once!
  end

  def app
    KaraokeWeb::App
  end

  # Stub Pipeline.enqueue so /pick and /admin/retry don't fire real Music.app
  # + render work. Saves the real method and restores it in teardown so the
  # *next* test (and PipelineTest itself, which does exercise the real
  # enqueue) gets the authentic module back.
  def stub_pipeline_enqueue!
    @__orig_enqueue ||= KaraokeWeb::Pipeline.method(:enqueue)
    @__pipeline_calls = []
    calls = @__pipeline_calls
    KaraokeWeb::Pipeline.define_singleton_method(:enqueue) do |id, async: true|
      calls << id
      nil
    end
    calls
  end

  def restore_pipeline_enqueue!
    return unless @__orig_enqueue
    orig = @__orig_enqueue
    KaraokeWeb::Pipeline.define_singleton_method(:enqueue, &orig)
    @__orig_enqueue = nil
  end

  def teardown
    restore_pipeline_enqueue!
    super
  end

  # ---- participant flow ------------------------------------------------

  def test_root_shows_join_form_when_unsigned_in
    get "/"
    assert_equal 200, last_response.status
    assert_match(/Pick your karaoke name/, last_response.body)
  end

  def test_join_creates_participant_and_redirects
    post "/join", name: "Craig"
    assert last_response.redirect?, "expected a redirect, got #{last_response.status}"
    follow_redirect!
    assert_match(/signed in as/, last_response.body)
    assert_match(/Craig/, last_response.body)
    assert_match(/Pick a song/, last_response.body)
  end

  def test_join_requires_non_empty_name
    post "/join", name: ""
    assert_equal 400, last_response.status
  end

  def test_join_rejects_overly_long_name
    post "/join", name: "x" * 41
    assert_equal 400, last_response.status
  end

  def test_pick_requires_join
    post "/pick", song_id: 1
    assert_equal 401, last_response.status
  end

  def test_participant_can_pick_a_song
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]
    assert last_response.redirect?, "expected a redirect, got #{last_response.status}"

    get "/stage.json"
    payload = JSON.parse(last_response.body)
    assert_equal 1, payload["queue"].length
    assert_equal "Craig", payload["queue"][0]["performer"]
    assert_equal true, payload["queue"][0]["ready"]
  end

  def test_participant_cannot_stack_two_picks
    post "/join", name: "Craig"
    songs = KaraokeWeb::DB.connection.execute("SELECT id FROM songs ORDER BY title")
    post "/pick", song_id: songs[0]["id"]
    post "/pick", song_id: songs[1]["id"]
    assert_equal 400, last_response.status

    get "/stage.json"
    payload = JSON.parse(last_response.body)
    assert_equal 1, payload["queue"].length, "second pick should have been rejected"
  end

  def test_pick_rejects_unknown_song_id
    post "/join", name: "Craig"
    post "/pick", song_id: 99999
    assert_equal 404, last_response.status
  end

  # ---- ad-hoc pick + search -------------------------------------------

  def test_pick_accepts_ad_hoc_title_and_enqueues_pipeline
    calls = stub_pipeline_enqueue!

    post "/join", name: "Craig"
    post "/pick", title: "Brand New Song", artist: "Fresh Band", album: "Debut"
    assert last_response.redirect?, "ad-hoc pick should succeed"

    # Song row was created with status=pending (no MP4 on disk).
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT status, artist, album FROM songs WHERE title = 'Brand New Song'"
    )
    refute_nil row
    assert_equal "pending", row["status"]
    assert_equal "Fresh Band", row["artist"]
    assert_equal "Debut", row["album"]

    refute_empty calls, "Pipeline.enqueue should be called for a not-yet-ready pick"
  end

  def test_pick_with_ad_hoc_title_returns_json_for_xhr
    stub_pipeline_enqueue!
    post "/join", name: "Craig"
    post "/pick", { title: "Xhr Song", artist: "A" }, { "HTTP_X_REQUESTED_WITH" => "XMLHttpRequest" }
    assert_equal 200, last_response.status
    payload = JSON.parse(last_response.body)
    assert payload["ok"]
    assert_kind_of Integer, payload["song_id"]
  end

  def test_songs_search_combines_library_and_catalog_and_dedupes
    KaraokeWeb::ItunesSearch.http_fetcher = ->(_term, _limit) {
      {
        results: [
          # Duplicate of a library song (Ocean Man) — should be removed.
          { kind: "song", trackName: "Ocean Man", artistName: "Ween",
            collectionName: "The Mollusk", releaseDate: "1997-06-24" },
          # Genuinely new catalog hit.
          { kind: "song", trackName: "The Mollusk", artistName: "Ween",
            collectionName: "The Mollusk", releaseDate: "1997-06-24" },
        ],
      }.to_json
    }
    # Give the library song a known artist so the dedup key matches.
    KaraokeWeb::DB.connection.execute(
      "UPDATE songs SET artist = 'Ween' WHERE title = 'Ocean Man'"
    )

    get "/songs/search", q: "ween"
    assert_equal 200, last_response.status
    data = JSON.parse(last_response.body)
    assert data["ready"].any? { |r| r["title"] == "Ocean Man" },
      "library hit should be in ready[]"
    titles = data["catalog"].map { |c| c["title"] }
    refute_includes titles, "Ocean Man", "duplicate catalog hit should be deduped"
    assert_includes titles, "The Mollusk"
  ensure
    KaraokeWeb::ItunesSearch.http_fetcher = nil
  end

  def test_songs_search_400_on_empty_query
    get "/songs/search", q: ""
    assert_equal 400, last_response.status
  end

  def test_songs_search_prefers_music_app_local_over_catalog
    # Music.app reports one downloaded match; iTunes catalog reports
    # (a) a duplicate of the local hit and (b) a genuinely new track.
    # The local hit should appear in `local`, the duplicate should be
    # dropped from `catalog`, and the new catalog track should remain.
    # Also point the filesystem walker at an empty dir so the real
    # ~/Music tree doesn't contaminate the test.
    KaraokeWeb::MusicApp.fs_roots = [File.join(@_tmp, "empty-roots")]
    FileUtils.mkdir_p(KaraokeWeb::MusicApp.fs_roots.first)
    KaraokeWeb::MusicApp.invalidate_fs_index!

    KaraokeWeb::MusicApp.script_runner = ->(_) {
      "Cool Song\tCool Band\tCool Album\t#{__FILE__}\n"
    }
    KaraokeWeb::ItunesSearch.http_fetcher = ->(_, _) {
      {
        results: [
          { kind: "song", trackName: "Cool Song", artistName: "Cool Band",
            collectionName: "Cool Album" },
          { kind: "song", trackName: "Different Song", artistName: "Cool Band",
            collectionName: "Other Album", releaseDate: "2020-01-01" },
        ],
      }.to_json
    }
    get "/songs/search", q: "cool"
    data = JSON.parse(last_response.body)
    assert_equal 1, data["local"].length
    assert_equal "Cool Song", data["local"][0]["title"]
    catalog_titles = data["catalog"].map { |c| c["title"] }
    refute_includes catalog_titles, "Cool Song"
    assert_includes catalog_titles, "Different Song"
  ensure
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::ItunesSearch.http_fetcher = nil
    KaraokeWeb::MusicApp.fs_roots = nil
    KaraokeWeb::MusicApp.invalidate_fs_index!
  end

  def test_songs_search_local_dedups_against_library
    # Our rendered library already has this title+artist → local tier
    # should drop it too, since `ready` outranks everything.
    KaraokeWeb::MusicApp.fs_roots = [File.join(@_tmp, "empty-roots")]
    FileUtils.mkdir_p(KaraokeWeb::MusicApp.fs_roots.first)
    KaraokeWeb::MusicApp.invalidate_fs_index!

    KaraokeWeb::DB.connection.execute(
      "UPDATE songs SET artist = 'Ween' WHERE title = 'Ocean Man'"
    )
    KaraokeWeb::MusicApp.script_runner = ->(_) {
      "Ocean Man\tWeen\tThe Mollusk\t#{__FILE__}\n"
    }
    KaraokeWeb::ItunesSearch.http_fetcher = ->(_, _) { { results: [] }.to_json }
    get "/songs/search", q: "ocean"
    data = JSON.parse(last_response.body)
    assert data["ready"].any? { |r| r["title"] == "Ocean Man" }
    refute data["local"].any? { |r| r["title"] == "Ocean Man" },
      "local tier must dedup against ready tier"
  ensure
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::ItunesSearch.http_fetcher = nil
    KaraokeWeb::MusicApp.fs_roots = nil
    KaraokeWeb::MusicApp.invalidate_fs_index!
  end

  # ---- /unpick + failed-pick escape -----------------------------------

  def test_unpick_requires_join
    post "/unpick"
    assert_equal 401, last_response.status
  end

  def test_unpick_marks_pending_entries_skipped
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]

    post "/unpick"
    assert last_response.redirect?

    get "/me.json"
    assert_equal 0, JSON.parse(last_response.body)["picks"].length,
      "after /unpick the participant has no active picks"

    # Entry still exists in the DB with state='skipped' (audit trail).
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT state FROM queue_entries ORDER BY id DESC LIMIT 1"
    )
    assert_equal "skipped", row["state"]
  end

  def test_unpick_xhr_returns_json
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]

    post "/unpick", {}, { "HTTP_X_REQUESTED_WITH" => "XMLHttpRequest" }
    assert_equal 200, last_response.status
    payload = JSON.parse(last_response.body)
    assert payload["ok"]
    assert_equal 1, payload["cancelled"]
  end

  def test_pick_unblocks_when_prior_pick_is_failed
    stub_pipeline_enqueue!

    post "/join", name: "Craig"
    pid = KaraokeWeb::DB.connection.get_first_row(
      "SELECT id FROM participants WHERE name = 'Craig'"
    )["id"]

    # Plant a failed prior pick directly — simulating a pipeline failure.
    failed_sid = KaraokeWeb::DB.upsert_song(title: "Broken", status: "failed")
    KaraokeWeb::DB.connection.execute(
      "INSERT INTO queue_entries(participant_id, song_id, position, state) VALUES (?, ?, 1, 'pending')",
      [pid, failed_sid]
    )

    # Without the failed-pick escape this would 400 with "Finish your current pick first".
    post "/pick", title: "Redemption Song", artist: "Bob Marley"
    assert last_response.redirect?, "pick should succeed when prior pick is failed"

    # Prior entry should now be marked skipped.
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT state FROM queue_entries WHERE song_id = ?", [failed_sid]
    )
    assert_equal "skipped", row["state"]

    # Participant now has exactly one live pick — the new one.
    get "/me.json"
    picks = JSON.parse(last_response.body)["picks"]
    assert_equal 1, picks.length
    assert_equal "Redemption Song", picks[0]["title"]
  end

  # ---- filesystem fallback + /pick path whitelist -----------------

  def with_fake_music_roots
    tree = File.join(@_tmp, "music-roots")
    FileUtils.mkdir_p(File.join(tree, "Oasis", "(What's The Story) Morning Glory_"))
    FileUtils.touch(File.join(tree, "Oasis", "(What's The Story) Morning Glory_", "04 Don't Look Back In Anger.m4a"))
    KaraokeWeb::MusicApp.fs_roots = [tree]
    KaraokeWeb::MusicApp.invalidate_fs_index!
    yield tree
  ensure
    KaraokeWeb::MusicApp.fs_roots = nil
    KaraokeWeb::MusicApp.invalidate_fs_index!
  end

  def test_search_surfaces_filesystem_hits_in_local_tier
    # Music.app AppleScript finds nothing, but the fs walk finds it.
    KaraokeWeb::MusicApp.script_runner = ->(_) { "" }
    KaraokeWeb::ItunesSearch.http_fetcher = ->(_, _) { { results: [] }.to_json }
    with_fake_music_roots do
      get "/songs/search", q: "don't look back"
      data = JSON.parse(last_response.body)
      refute_empty data["local"], "filesystem hit must surface in the local tier"
      hit = data["local"][0]
      assert_equal "Don't Look Back In Anger", hit["title"]
      assert_equal "Oasis", hit["artist"]
      assert hit["path"].to_s.end_with?("04 Don't Look Back In Anger.m4a")
    end
  ensure
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::ItunesSearch.http_fetcher = nil
  end

  def test_search_dedups_filesystem_vs_music_app_by_path
    with_fake_music_roots do |tree|
      shared_path = File.join(tree, "Oasis", "(What's The Story) Morning Glory_",
                              "04 Don't Look Back In Anger.m4a")
      KaraokeWeb::MusicApp.script_runner = ->(_) {
        "Don't Look Back In Anger\tOasis\tMorning Glory\t#{shared_path}\n"
      }
      KaraokeWeb::ItunesSearch.http_fetcher = ->(_, _) { { results: [] }.to_json }
      get "/songs/search", q: "don't"
      data = JSON.parse(last_response.body)
      assert_equal 1, data["local"].length, "dedup by path must collapse music-app + fs duplicates"
    end
  ensure
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::ItunesSearch.http_fetcher = nil
  end

  def test_pick_with_whitelisted_path_stores_audio_path
    stub_pipeline_enqueue!
    with_fake_music_roots do |tree|
      audio = File.join(tree, "Oasis", "(What's The Story) Morning Glory_",
                        "04 Don't Look Back In Anger.m4a")
      post "/join", name: "Craig"
      post "/pick",
        title: "Don't Look Back In Anger", artist: "Oasis",
        album: "Morning Glory", path: audio
      assert last_response.redirect?

      row = KaraokeWeb::DB.connection.get_first_row(
        "SELECT audio_path FROM songs WHERE title = ?",
        ["Don't Look Back In Anger"]
      )
      assert_equal File.realpath(audio), row["audio_path"]
    end
  end

  def test_pick_rejects_path_outside_whitelisted_roots
    stub_pipeline_enqueue!
    outside = File.join(@_tmp, "escape.m4a")
    FileUtils.touch(outside)
    with_fake_music_roots do
      post "/join", name: "Craig"
      post "/pick", title: "Bad", artist: "Nope", path: outside
      row = KaraokeWeb::DB.connection.get_first_row(
        "SELECT audio_path FROM songs WHERE title = 'Bad'"
      )
      assert_nil row["audio_path"], "paths outside the music roots must be ignored"
    end
  end

  # ---- admin /fix + /delete-song ------------------------------------

  def test_admin_fix_requires_auth
    post "/admin/fix/1", path: "/tmp/nope.mp3"
    assert_equal 302, last_response.status
  end

  def test_admin_fix_sets_audio_path_and_reenqueues
    calls = stub_pipeline_enqueue!
    audio = File.join(@_tmp, "real.m4a")
    FileUtils.touch(audio)
    sid = KaraokeWeb::DB.upsert_song(title: "Broken", artist: "x", status: "failed")
    KaraokeWeb::DB.connection.execute(
      "UPDATE songs SET error = 'some error' WHERE id = ?", [sid]
    )

    post "/admin/login", password: "Austin"
    post "/admin/fix/#{sid}", path: audio
    assert last_response.redirect?

    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT audio_path, status, error FROM songs WHERE id = ?", [sid]
    )
    assert_equal File.realpath(audio), row["audio_path"]
    assert_equal "pending", row["status"]
    assert_nil row["error"]
    assert_includes calls, sid
  end

  def test_admin_fix_400_on_missing_path
    post "/admin/login", password: "Austin"
    sid = KaraokeWeb::DB.upsert_song(title: "Broken", status: "failed")
    post "/admin/fix/#{sid}", path: "/tmp/does-not-exist-#{rand}.m4a"
    assert_equal 400, last_response.status
  end

  def test_admin_fix_400_on_unsupported_extension
    post "/admin/login", password: "Austin"
    sid = KaraokeWeb::DB.upsert_song(title: "Broken", status: "failed")
    bad = File.join(@_tmp, "notes.txt")
    FileUtils.touch(bad)
    post "/admin/fix/#{sid}", path: bad
    assert_equal 400, last_response.status
  end

  def test_admin_fix_accepts_shell_escaped_path
    # Users often paste paths from Terminal with `\ ` and `\(` escapes.
    # Our normalizer should strip them before the filesystem check.
    stub_pipeline_enqueue!
    dir = File.join(@_tmp, "Has Spaces (And Parens)")
    FileUtils.mkdir_p(dir)
    audio = File.join(dir, "What's Up.m4a")
    FileUtils.touch(audio)

    sid = KaraokeWeb::DB.upsert_song(title: "Spaces", status: "failed")
    post "/admin/login", password: "Austin"

    shell_style = audio
      .gsub(" ")  { "\\ " }
      .gsub("(")  { "\\(" }
      .gsub(")")  { "\\)" }
      .gsub("'")  { "\\'" }
    post "/admin/fix/#{sid}", path: shell_style
    assert last_response.redirect?, "shell-escaped path should resolve. body=#{last_response.body}"

    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT audio_path FROM songs WHERE id = ?", [sid]
    )
    assert_equal File.realpath(audio), row["audio_path"]
  end

  def test_admin_fix_accepts_quoted_path
    stub_pipeline_enqueue!
    audio = File.join(@_tmp, "quoted.m4a")
    FileUtils.touch(audio)
    sid = KaraokeWeb::DB.upsert_song(title: "Q", status: "failed")
    post "/admin/login", password: "Austin"
    post "/admin/fix/#{sid}", path: "\"#{audio}\""
    assert last_response.redirect?
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT audio_path FROM songs WHERE id = ?", [sid]
    )
    assert_equal File.realpath(audio), row["audio_path"]
  end

  def test_admin_fix_expands_tilde_paths
    stub_pipeline_enqueue!
    # Use a path inside our tmp dir but expressed with $HOME so we
    # exercise File.expand_path. (Direct ~ expansion depends on the
    # running user's $HOME, which Rack::Test inherits.)
    audio = File.join(@_tmp, "tilde.m4a")
    FileUtils.touch(audio)
    sid = KaraokeWeb::DB.upsert_song(title: "T", status: "failed")
    post "/admin/login", password: "Austin"
    post "/admin/fix/#{sid}", path: audio # already absolute, expand_path is a no-op
    assert last_response.redirect?
  end

  def test_admin_delete_song_cascades_queue_entries
    post "/admin/login", password: "Austin"
    sid = KaraokeWeb::DB.upsert_song(title: "Doomed", status: "failed")
    KaraokeWeb::DB.connection.execute(
      "INSERT INTO participants(name) VALUES ('x')"
    )
    pid = KaraokeWeb::DB.connection.last_insert_row_id
    KaraokeWeb::DB.connection.execute(
      "INSERT INTO queue_entries(participant_id, song_id, position) VALUES (?, ?, 1)",
      [pid, sid]
    )

    post "/admin/delete-song/#{sid}"
    assert last_response.redirect?

    song_row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT COUNT(*) AS n FROM songs WHERE id = ?", [sid]
    )
    qe_row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT COUNT(*) AS n FROM queue_entries WHERE song_id = ?", [sid]
    )
    assert_equal 0, song_row["n"]
    assert_equal 0, qe_row["n"], "ON DELETE CASCADE must drop queue entries"
  end

  def test_admin_delete_song_requires_auth
    sid = KaraokeWeb::DB.upsert_song(title: "Nope", status: "failed")
    post "/admin/delete-song/#{sid}"
    assert_equal 302, last_response.status
  end

  def test_admin_rescan_invalidates_fs_index
    post "/admin/login", password: "Austin"
    with_fake_music_roots do
      KaraokeWeb::MusicApp.fs_index # prime the cache
      post "/admin/rescan"
      assert_nil KaraokeWeb::MusicApp.instance_variable_get(:@fs_index),
        "rescan must clear the filesystem search cache"
    end
  end

  def test_pipeline_prefers_audio_path_over_music_app
    # When a song row has an audio_path pointing to a real file, the
    # pipeline should render from that path and skip Music.app entirely.
    audio = File.join(@_tmp, "supplied.m4a")
    FileUtils.touch(audio)
    KaraokeWeb::MusicApp.script_runner = ->(_) {
      raise "Music.app must not be consulted when audio_path is set"
    }
    rendered = []
    KaraokeWeb::Pipeline.renderer = ->(cmd) {
      rendered << cmd
      assert_includes cmd, audio, "renderer must be pointed at the supplied audio file"
      FileUtils.touch(File.join(@video_root, "Fixed Up.mp4"))
      ["", "", Class.new { def success?; true; end }.new]
    }

    id = KaraokeWeb::DB.upsert_song(
      title: "Fixed Up", artist: "x", audio_path: audio, status: "pending"
    )
    KaraokeWeb::Pipeline.enqueue(id, async: false)

    assert_equal 1, rendered.length
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT status FROM songs WHERE id = ?", [id]
    )
    assert_equal "ready", row["status"]
  ensure
    KaraokeWeb::MusicApp.script_runner = nil
    KaraokeWeb::Pipeline.renderer = nil
  end

  def test_pick_still_blocked_when_prior_pick_is_performing
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]
    qid = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM queue_entries")["id"]
    KaraokeWeb::DB.connection.execute(
      "UPDATE queue_entries SET state = 'performing' WHERE id = ?", [qid]
    )

    post "/pick", title: "Sneaky Second", artist: "Someone"
    assert_equal 400, last_response.status
    assert_match(/admin/, last_response.body)
  end

  # ---- /me.json --------------------------------------------------------

  def test_me_json_requires_join
    get "/me.json"
    assert_equal 401, last_response.status
  end

  def test_me_json_exposes_song_status
    # Create a failed song row so /me.json reports the error back to the
    # participant's phone.
    post "/join", name: "Craig"
    sid = KaraokeWeb::DB.upsert_song(title: "Flaky", status: "failed")
    KaraokeWeb::DB.connection.execute(
      "UPDATE songs SET error = 'Music.app missing' WHERE id = ?", [sid]
    )
    KaraokeWeb::DB.connection.execute(
      "INSERT INTO queue_entries(participant_id, song_id, position, state) " \
      "VALUES ((SELECT id FROM participants WHERE name = 'Craig'), ?, 1, 'pending')",
      [sid]
    )

    get "/me.json"
    data = JSON.parse(last_response.body)
    assert_equal 1, data["picks"].length
    p = data["picks"][0]
    assert_equal "failed", p["song_status"]
    assert_equal "Music.app missing", p["song_error"]
    assert_equal false, p["ready"]
  end

  # ---- admin retry ----------------------------------------------------

  def test_admin_retry_requires_auth
    post "/admin/retry/1"
    assert_equal 302, last_response.status
  end

  def test_admin_retry_resets_status_and_enqueues
    calls = stub_pipeline_enqueue!
    sid = KaraokeWeb::DB.upsert_song(title: "Broken", status: "failed")
    KaraokeWeb::DB.connection.execute("UPDATE songs SET error = 'x' WHERE id = ?", [sid])

    post "/admin/login", password: "Austin"
    post "/admin/retry/#{sid}"
    assert last_response.redirect?

    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT status, error FROM songs WHERE id = ?", [sid]
    )
    assert_equal "pending", row["status"]
    assert_nil row["error"]
    assert_includes calls, sid
  end

  def test_admin_retry_404_for_unknown_song
    post "/admin/login", password: "Austin"
    post "/admin/retry/99999"
    assert_equal 404, last_response.status
  end

  # ---- stage -----------------------------------------------------------

  def test_stage_renders_qr_and_placeholder_queue
    get "/stage"
    assert_equal 200, last_response.status
    assert_match(/scan to join/, last_response.body)
    assert_match(/<svg/, last_response.body) # QR code inline
    assert_match(/queue is empty|waiting for the next singer|now performing/, last_response.body)
  end

  def test_stage_json_shape
    get "/stage.json"
    assert_equal 200, last_response.status
    payload = JSON.parse(last_response.body)
    assert_equal [], payload["queue"]
    assert_nil payload["now_playing"]
    assert_equal true, payload["admin_required"]
  end

  # ---- admin -----------------------------------------------------------

  def test_admin_requires_login
    get "/admin"
    assert_equal 302, last_response.status
    assert_match %r{/admin/login}, last_response.headers["Location"]
  end

  def test_admin_login_rejects_wrong_password
    post "/admin/login", password: "nope"
    assert_equal 401, last_response.status
    assert_match(/Wrong password/, last_response.body)
  end

  def test_admin_login_accepts_default_austin
    ENV.delete("KARAOKE_ADMIN_PASSWORD")
    post "/admin/login", password: "Austin"
    assert last_response.redirect?, "expected a redirect, got #{last_response.status}"
    get "/admin"
    assert_equal 200, last_response.status
  end

  def test_admin_lifecycle_start_then_done
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]
    qid = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM queue_entries LIMIT 1")["id"]

    # Clear cookies so admin session is independent of participant session.
    clear_cookies
    post "/admin/login", password: "Austin"
    post "/admin/start/#{qid}"

    get "/stage.json"
    payload = JSON.parse(last_response.body)
    assert_equal "performing", payload["now_playing"]["state"]

    post "/admin/done/#{qid}"
    get "/stage.json"
    payload = JSON.parse(last_response.body)
    assert_nil payload["now_playing"]
    assert_equal 0, payload["queue"].length
  end

  def test_admin_skip_and_remove
    post "/join", name: "Craig"
    songs = KaraokeWeb::DB.connection.execute("SELECT id FROM songs ORDER BY title LIMIT 2")
    post "/pick", song_id: songs[0]["id"]
    qid = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM queue_entries LIMIT 1")["id"]

    clear_cookies
    post "/admin/login", password: "Austin"
    post "/admin/skip/#{qid}"
    get "/stage.json"
    assert_equal [], JSON.parse(last_response.body)["queue"],
      "skipped entries drop off the live queue"

    # Remove is a hard delete — should be fine on an already-skipped row.
    post "/admin/remove/#{qid}"
    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT COUNT(*) AS n FROM queue_entries WHERE id = ?", [qid]
    )
    assert_equal 0, row["n"]
  end

  def test_admin_actions_require_auth
    post "/admin/start/1"
    assert_equal 302, last_response.status
    post "/admin/done/1"
    assert_equal 302, last_response.status
    post "/admin/start-next"
    assert_equal 302, last_response.status
  end

  def test_start_next_promotes_top_of_queue
    # Two participants pick; start-next should promote the first one.
    post "/join", name: "Alice"
    songs = KaraokeWeb::DB.connection.execute("SELECT id FROM songs ORDER BY title LIMIT 2")
    post "/pick", song_id: songs[0]["id"]
    clear_cookies
    post "/join", name: "Bob"
    post "/pick", song_id: songs[1]["id"]
    clear_cookies

    post "/admin/login", password: "Austin"
    post "/admin/start-next"
    assert last_response.redirect?

    get "/stage.json"
    payload = JSON.parse(last_response.body)
    assert_equal "Alice", payload["now_playing"]["performer"],
      "start-next should promote the lowest-position pending row"
    assert_equal 1, payload["queue"].count { |q| q["state"] == "pending" }
  end

  def test_start_next_noops_when_queue_empty
    post "/admin/login", password: "Austin"
    post "/admin/start-next"
    assert last_response.redirect?
    get "/stage.json"
    assert_nil JSON.parse(last_response.body)["now_playing"]
  end

  def test_stage_payload_includes_song_id_for_playback
    post "/join", name: "Craig"
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs ORDER BY title LIMIT 1")
    post "/pick", song_id: song["id"]
    clear_cookies

    post "/admin/login", password: "Austin"
    qid = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM queue_entries LIMIT 1")["id"]
    post "/admin/start/#{qid}"

    get "/stage.json"
    payload = JSON.parse(last_response.body)
    np = payload["now_playing"]
    refute_nil np
    assert_equal song["id"], np["song_id"],
      "now_playing must expose song_id so the stage player can load /video/<song_id>"
  end

  def test_stage_exposes_admin_flag_only_when_logged_in
    get "/stage"
    refute_match(/__STAGE_IS_ADMIN__\s*=\s*true/, last_response.body)

    post "/admin/login", password: "Austin"
    get "/stage"
    assert_match(/__STAGE_IS_ADMIN__\s*=\s*true/, last_response.body)
    # And the "Start next performance" button is now present.
    assert_match(/Start next performance/, last_response.body)
  end

  def test_admin_stage_has_autoplay_arm_and_overlay
    post "/admin/login", password: "Austin"
    get "/stage"
    body = last_response.body
    # The video element must have `autoplay` so modern browsers treat
    # programmatic play() inside a gesture-armed tab as allowed.
    assert_match(/<video[^>]*\bautoplay\b/, body)
    assert_match(/id="stageArm"/, body)
    assert_match(/id="stageArmBtn"/, body)
    assert_match(/id="stagePlayOverlay"/, body)
  end

  def test_non_admin_stage_has_no_player_elements
    get "/stage"
    body = last_response.body
    refute_match(/<video/, body)
    refute_match(/id="stageArm"/, body)
    refute_match(/id="stagePlayOverlay"/, body)
  end

  # ---- video serving --------------------------------------------------

  def test_video_route_serves_library_files
    song = KaraokeWeb::DB.connection.get_first_row(
      "SELECT id FROM songs WHERE title = 'Ocean Man'"
    )
    get "/video/#{song["id"]}"
    assert_equal 200, last_response.status
    assert_equal "video/mp4", last_response.headers["Content-Type"]
  end

  def test_video_route_404_for_unknown_song
    get "/video/99999"
    assert_equal 404, last_response.status
  end

  def test_video_route_blocks_paths_outside_library_root
    # Plant a song row whose video_path points outside the library root;
    # the realpath check must reject it.
    escape = File.join(@_tmp, "escape.mp4")
    FileUtils.touch(escape)
    KaraokeWeb::DB.upsert_song(title: "Escape", video_path: escape, status: "ready")
    song = KaraokeWeb::DB.connection.get_first_row("SELECT id FROM songs WHERE title = 'Escape'")
    get "/video/#{song["id"]}"
    assert_equal 404, last_response.status
  end
end
