require "net/http"
require "json"
require "uri"

module KaraokeWeb
  # Thin Ruby client for the public iTunes Search API — same endpoint the
  # Python code uses (itunes.py). No auth required. Returns normalized
  # {title, artist, album, year, track_id} hashes.
  module ItunesSearch
    SEARCH_URL = "https://itunes.apple.com/search".freeze
    DEFAULT_LIMIT = 10
    TIMEOUT = 5.0

    class << self
      # Tests can replace this with a stub instead of hitting the network.
      attr_accessor :http_fetcher

      def search(term, limit: DEFAULT_LIMIT)
        term = term.to_s.strip
        return [] if term.empty?

        body = (http_fetcher || method(:default_fetch)).call(term, limit)
        return [] if body.nil? || body.empty?
        parse(JSON.parse(body))
      rescue StandardError => e
        warn "[itunes] search failed: #{e.class}: #{e.message}"
        []
      end

      def parse(data)
        Array(data["results"]).filter_map do |r|
          next unless r["kind"] == "song"
          title = r["trackName"].to_s.strip
          next if title.empty?
          artist = r["artistName"].to_s.strip
          album = r["collectionName"].to_s.strip
          year = r["releaseDate"].to_s[0, 4]
          {
            title: title,
            artist: artist,
            album: album,
            year: year.match?(/^\d{4}$/) ? year : nil,
            track_id: r["trackId"],
          }
        end
      end

      def default_fetch(term, limit)
        uri = URI(SEARCH_URL)
        uri.query = URI.encode_www_form(
          term: term, entity: "song", limit: limit.to_s, media: "music"
        )
        req = Net::HTTP::Get.new(uri)
        req["User-Agent"] = "mon-amigo-karaoke-web/0.1"
        res = Net::HTTP.start(
          uri.host, uri.port,
          use_ssl: uri.scheme == "https",
          open_timeout: TIMEOUT,
          read_timeout: TIMEOUT,
        ) { |http| http.request(req) }
        res.is_a?(Net::HTTPSuccess) ? res.body : nil
      end
    end
  end
end
