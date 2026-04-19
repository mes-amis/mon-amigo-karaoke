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
