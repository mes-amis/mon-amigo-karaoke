require "net/http"
require "json"
require "uri"

module KaraokeWeb
  # Reads the public tunnel URL from ngrok's local API
  # (http://127.0.0.1:4040/api/tunnels). Returns nil when ngrok isn't running
  # so callers can fall back to a localhost URL for QR rendering.
  module Ngrok
    API_URL = "http://127.0.0.1:4040/api/tunnels"
    CACHE_TTL = 5 # seconds

    class << self
      def public_url
        now = Time.now.to_f
        if @cached_at && (now - @cached_at) < CACHE_TTL
          return @cached_url
        end
        @cached_at = now
        @cached_url = fetch_public_url
      end

      def fetch_public_url
        uri = URI(API_URL)
        res = Net::HTTP.start(uri.host, uri.port, open_timeout: 0.3, read_timeout: 0.5) do |http|
          http.get(uri.request_uri)
        end
        return nil unless res.is_a?(Net::HTTPSuccess)
        data = JSON.parse(res.body)
        tunnels = Array(data["tunnels"])
        https = tunnels.find { |t| t["public_url"].to_s.start_with?("https://") }
        (https || tunnels.first)&.dig("public_url")
      rescue StandardError
        nil
      end
    end
  end
end
