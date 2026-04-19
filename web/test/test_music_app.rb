require_relative "test_helper"
require_relative "../lib/music_app"

class MusicAppTest < Minitest::Test
  def teardown
    KaraokeWeb::MusicApp.script_runner = nil
  end

  def test_find_local_returns_nil_on_empty_inputs
    KaraokeWeb::MusicApp.script_runner = ->(_) { raise "should not be called" }
    assert_nil KaraokeWeb::MusicApp.find_local(title: "", artist: "x")
    assert_nil KaraokeWeb::MusicApp.find_local(title: "x", artist: "")
  end

  def test_find_local_returns_path_when_script_returns_a_real_file
    path = __FILE__ # any real file this test can see
    KaraokeWeb::MusicApp.script_runner = ->(script) {
      assert_match(/name is "Ocean Man" and artist is "Ween"/, script)
      path
    }
    assert_equal path, KaraokeWeb::MusicApp.find_local(title: "Ocean Man", artist: "Ween")
  end

  def test_find_local_returns_nil_when_script_returns_nonexistent_path
    KaraokeWeb::MusicApp.script_runner = ->(_) { "/tmp/not-a-real-file-#{rand}.m4a" }
    assert_nil KaraokeWeb::MusicApp.find_local(title: "x", artist: "y")
  end

  def test_find_local_returns_nil_when_script_returns_empty
    KaraokeWeb::MusicApp.script_runner = ->(_) { "" }
    assert_nil KaraokeWeb::MusicApp.find_local(title: "x", artist: "y")
  end

  def test_find_local_escapes_quotes
    seen = nil
    KaraokeWeb::MusicApp.script_runner = ->(script) {
      seen = script
      ""
    }
    KaraokeWeb::MusicApp.find_local(title: 'Say "Hello"', artist: "Them")
    assert_match(/name is "Say \\"Hello\\""/, seen)
  end

  def test_trigger_download_reports_ok
    KaraokeWeb::MusicApp.script_runner = ->(script) {
      assert_match(/download t/, script)
      "ok"
    }
    assert_equal true, KaraokeWeb::MusicApp.trigger_download(title: "x", artist: "y")
  end

  def test_trigger_download_reports_missing_as_false
    KaraokeWeb::MusicApp.script_runner = ->(_) { "missing" }
    assert_equal false, KaraokeWeb::MusicApp.trigger_download(title: "x", artist: "y")
  end

  def test_trigger_download_reports_applescript_error_as_false
    KaraokeWeb::MusicApp.script_runner = ->(_) { "error:locked" }
    assert_equal false, KaraokeWeb::MusicApp.trigger_download(title: "x", artist: "y")
  end

  def test_trigger_download_refuses_blank_inputs
    KaraokeWeb::MusicApp.script_runner = ->(_) { raise "should not be called" }
    refute KaraokeWeb::MusicApp.trigger_download(title: "", artist: "y")
    refute KaraokeWeb::MusicApp.trigger_download(title: "x", artist: "")
  end

  def test_search_library_returns_empty_on_blank_query
    KaraokeWeb::MusicApp.script_runner = ->(_) { raise "should not be called" }
    assert_equal [], KaraokeWeb::MusicApp.search_library("")
    assert_equal [], KaraokeWeb::MusicApp.search_library("   ")
  end

  def test_search_library_parses_tab_separated_rows
    path = __FILE__
    KaraokeWeb::MusicApp.script_runner = ->(script) {
      assert_match(/contains q/, script)
      "Ocean Man\tWeen\tThe Mollusk\t#{path}\n" \
      "Roses Are Free\tWeen\tChocolate and Cheese\t#{path}\n"
    }
    hits = KaraokeWeb::MusicApp.search_library("ween")
    assert_equal 2, hits.length
    assert_equal "Ocean Man", hits[0][:title]
    assert_equal "Ween", hits[0][:artist]
    assert_equal "The Mollusk", hits[0][:album]
    assert_equal path, hits[0][:path]
  end

  def test_search_library_drops_rows_with_missing_files
    KaraokeWeb::MusicApp.script_runner = ->(_) {
      "Phantom Track\tGhost\tSpook\t/tmp/definitely-not-here-#{rand}.m4a\n" \
      "Real Track\tReal Artist\tReal Album\t#{__FILE__}\n"
    }
    hits = KaraokeWeb::MusicApp.search_library("x")
    assert_equal 1, hits.length
    assert_equal "Real Track", hits[0][:title]
  end

  def test_search_library_returns_empty_when_music_app_off
    KaraokeWeb::MusicApp.script_runner = ->(_) { "" }
    assert_equal [], KaraokeWeb::MusicApp.search_library("anything")
  end

  # ---- filesystem fallback ----

  def with_tmp_music_tree(&blk)
    root = Dir.mktmpdir("kk-music-")
    begin
      # <root>/Oasis/(What's The Story) Morning Glory_/04 Don't Look Back In Anger.m4a
      oasis_album = File.join(root, "Oasis", "(What's The Story) Morning Glory_")
      FileUtils.mkdir_p(oasis_album)
      FileUtils.touch(File.join(oasis_album, "04 Don't Look Back In Anger.m4a"))
      FileUtils.touch(File.join(oasis_album, "06 Wonderwall.m4a"))
      # A non-audio file we must ignore.
      FileUtils.touch(File.join(oasis_album, "cover.jpg"))
      # Another artist to test isolation.
      nirvana = File.join(root, "Nirvana", "Nevermind")
      FileUtils.mkdir_p(nirvana)
      FileUtils.touch(File.join(nirvana, "01 Smells Like Teen Spirit.m4a"))

      KaraokeWeb::MusicApp.fs_roots = [root]
      KaraokeWeb::MusicApp.invalidate_fs_index!
      yield root
    ensure
      KaraokeWeb::MusicApp.fs_roots = nil
      KaraokeWeb::MusicApp.invalidate_fs_index!
      FileUtils.remove_entry(root)
    end
  end

  def test_search_filesystem_finds_legacy_itunes_file
    with_tmp_music_tree do |root|
      hits = KaraokeWeb::MusicApp.search_filesystem("don't look back in anger")
      assert_equal 1, hits.length
      h = hits[0]
      assert_equal "Don't Look Back In Anger", h[:title]
      assert_equal "Oasis", h[:artist]
      assert_equal "(What's The Story) Morning Glory_", h[:album]
      assert_equal File.join(root, "Oasis", "(What's The Story) Morning Glory_",
                             "04 Don't Look Back In Anger.m4a"),
                   h[:path]
    end
  end

  def test_search_filesystem_matches_artist_substring
    with_tmp_music_tree do
      hits = KaraokeWeb::MusicApp.search_filesystem("oasis")
      assert_equal 2, hits.length
      assert_equal ["Don't Look Back In Anger", "Wonderwall"].sort,
                   hits.map { |h| h[:title] }.sort
    end
  end

  def test_search_filesystem_ignores_non_audio
    with_tmp_music_tree do
      hits = KaraokeWeb::MusicApp.search_filesystem("cover")
      assert_equal [], hits
    end
  end

  def test_search_filesystem_case_insensitive
    with_tmp_music_tree do
      assert_equal 1, KaraokeWeb::MusicApp.search_filesystem("NIRVANA").length
      assert_equal 1, KaraokeWeb::MusicApp.search_filesystem("smells").length
    end
  end

  def test_search_filesystem_strips_track_number_prefix
    with_tmp_music_tree do
      hit = KaraokeWeb::MusicApp.search_filesystem("teen spirit").first
      assert_equal "Smells Like Teen Spirit", hit[:title]
    end
  end

  def test_fs_index_cached_until_invalidated
    with_tmp_music_tree do |root|
      first = KaraokeWeb::MusicApp.fs_index
      # Add a file but don't invalidate — cached index should not see it.
      new_file = File.join(root, "Pearl Jam", "Ten", "01 Alive.m4a")
      FileUtils.mkdir_p(File.dirname(new_file))
      FileUtils.touch(new_file)
      second = KaraokeWeb::MusicApp.fs_index
      assert_same first, second, "fs_index should be cached by identity"
      refute second.any? { |e| e[:title] == "Alive" },
        "cached index shouldn't reflect new files until invalidated"

      KaraokeWeb::MusicApp.invalidate_fs_index!
      third = KaraokeWeb::MusicApp.fs_index
      assert third.any? { |e| e[:title] == "Alive" },
        "invalidate_fs_index! must force a rebuild"
    end
  end

  def test_search_filesystem_empty_query_returns_empty
    with_tmp_music_tree do
      assert_equal [], KaraokeWeb::MusicApp.search_filesystem("")
    end
  end

  def test_search_filesystem_skips_nonexistent_roots
    KaraokeWeb::MusicApp.fs_roots = ["/tmp/does-not-exist-#{rand(10_000_000)}"]
    KaraokeWeb::MusicApp.invalidate_fs_index!
    assert_equal [], KaraokeWeb::MusicApp.search_filesystem("x")
  ensure
    KaraokeWeb::MusicApp.fs_roots = nil
    KaraokeWeb::MusicApp.invalidate_fs_index!
  end
end
