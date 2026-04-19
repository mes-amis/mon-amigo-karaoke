require_relative "test_helper"
require_relative "../lib/itunes_search"

class ItunesSearchTest < Minitest::Test
  def teardown
    KaraokeWeb::ItunesSearch.http_fetcher = nil
  end

  def test_empty_query_returns_empty_without_network
    KaraokeWeb::ItunesSearch.http_fetcher = ->(*_) { raise "should not be called" }
    assert_equal [], KaraokeWeb::ItunesSearch.search("")
    assert_equal [], KaraokeWeb::ItunesSearch.search("   ")
  end

  def test_parses_song_results
    KaraokeWeb::ItunesSearch.http_fetcher = ->(term, limit) {
      assert_equal "ocean man", term
      assert_equal 5, limit
      {
        resultCount: 2,
        results: [
          { kind: "song", trackName: "Ocean Man", artistName: "Ween",
            collectionName: "The Mollusk", releaseDate: "1997-06-24T07:00:00Z",
            trackId: 123 },
          { kind: "song", trackName: "Ocean Man (Remix)", artistName: "Ween",
            collectionName: "Remixes", releaseDate: "2001-01-01",
            trackId: 456 },
        ],
      }.to_json
    }
    out = KaraokeWeb::ItunesSearch.search("ocean man", limit: 5)
    assert_equal 2, out.length
    assert_equal "Ocean Man", out[0][:title]
    assert_equal "Ween", out[0][:artist]
    assert_equal "The Mollusk", out[0][:album]
    assert_equal "1997", out[0][:year]
    assert_equal 123, out[0][:track_id]
  end

  def test_filters_non_song_results
    KaraokeWeb::ItunesSearch.http_fetcher = ->(*_) {
      {
        results: [
          { kind: "music-video", trackName: "A Video" },
          { kind: "song", trackName: "Real Song", artistName: "Someone" },
        ],
      }.to_json
    }
    out = KaraokeWeb::ItunesSearch.search("whatever")
    assert_equal 1, out.length
    assert_equal "Real Song", out[0][:title]
  end

  def test_swallows_http_errors_and_returns_empty
    KaraokeWeb::ItunesSearch.http_fetcher = ->(*_) { raise SocketError, "no dns" }
    assert_silent do
      # warn goes to $stderr; not asserting on it, just confirming no raise.
    end
    out = capture_io { @out = KaraokeWeb::ItunesSearch.search("x") }
    assert_equal [], @out
  end

  def test_nil_body_returns_empty
    KaraokeWeb::ItunesSearch.http_fetcher = ->(*_) { nil }
    assert_equal [], KaraokeWeb::ItunesSearch.search("x")
  end

  def test_ignores_empty_title_or_bad_year
    KaraokeWeb::ItunesSearch.http_fetcher = ->(*_) {
      {
        results: [
          { kind: "song", trackName: "", artistName: "Nope" },
          { kind: "song", trackName: "Good", artistName: "Band",
            releaseDate: "unknown" },
        ],
      }.to_json
    }
    out = KaraokeWeb::ItunesSearch.search("x")
    assert_equal 1, out.length
    assert_nil out[0][:year]
  end
end
