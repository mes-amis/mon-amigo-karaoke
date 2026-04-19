require_relative "test_helper"
require_relative "../lib/ngrok"
require "webrick"

class NgrokTest < Minitest::Test
  # A tiny in-process HTTP server that impersonates the ngrok local API
  # (http://127.0.0.1:4040/api/tunnels). We stand it up on a random port
  # and point Ngrok::API_URL at it for the duration of the test.
  def with_fake_api(body, status: 200)
    server = WEBrick::HTTPServer.new(
      Port: 0, BindAddress: "127.0.0.1",
      Logger: WEBrick::Log.new(nil, 0),
      AccessLog: [],
    )
    server.mount_proc("/api/tunnels") do |_req, res|
      res.status = status
      res["Content-Type"] = "application/json"
      res.body = body
    end
    thr = Thread.new { server.start }
    port = server.config[:Port]
    original = KaraokeWeb::Ngrok::API_URL
    KaraokeWeb::Ngrok.send(:remove_const, :API_URL)
    KaraokeWeb::Ngrok.const_set(:API_URL, "http://127.0.0.1:#{port}/api/tunnels")
    # Reset the per-process cache so prior tests don't leak.
    KaraokeWeb::Ngrok.instance_variable_set(:@cached_at, nil)
    KaraokeWeb::Ngrok.instance_variable_set(:@cached_url, nil)
    yield
  ensure
    server&.shutdown
    thr&.join(1)
    if original
      KaraokeWeb::Ngrok.send(:remove_const, :API_URL)
      KaraokeWeb::Ngrok.const_set(:API_URL, original)
    end
    KaraokeWeb::Ngrok.instance_variable_set(:@cached_at, nil)
    KaraokeWeb::Ngrok.instance_variable_set(:@cached_url, nil)
  end

  def test_returns_https_tunnel_when_present
    body = {
      tunnels: [
        { "public_url" => "http://abc.ngrok.io" },
        { "public_url" => "https://abc.ngrok.io" },
      ],
    }.to_json
    with_fake_api(body) do
      assert_equal "https://abc.ngrok.io", KaraokeWeb::Ngrok.fetch_public_url
    end
  end

  def test_returns_first_tunnel_if_no_https
    body = { tunnels: [{ "public_url" => "tcp://abc.ngrok.io" }] }.to_json
    with_fake_api(body) do
      assert_equal "tcp://abc.ngrok.io", KaraokeWeb::Ngrok.fetch_public_url
    end
  end

  def test_nil_when_no_tunnels
    with_fake_api({ tunnels: [] }.to_json) do
      assert_nil KaraokeWeb::Ngrok.fetch_public_url
    end
  end

  def test_nil_when_api_unreachable
    # Deliberately a port nothing's listening on — should be caught, not raise.
    KaraokeWeb::Ngrok.send(:remove_const, :API_URL)
    KaraokeWeb::Ngrok.const_set(:API_URL, "http://127.0.0.1:1/api/tunnels")
    KaraokeWeb::Ngrok.instance_variable_set(:@cached_at, nil)
    assert_nil KaraokeWeb::Ngrok.fetch_public_url
  end

  def test_public_url_caches_within_ttl
    calls = 0
    body1 = { tunnels: [{ "public_url" => "https://first.ngrok.io" }] }.to_json
    body2 = { tunnels: [{ "public_url" => "https://second.ngrok.io" }] }.to_json

    server = WEBrick::HTTPServer.new(
      Port: 0, BindAddress: "127.0.0.1",
      Logger: WEBrick::Log.new(nil, 0), AccessLog: [],
    )
    server.mount_proc("/api/tunnels") do |_req, res|
      calls += 1
      res.status = 200
      res["Content-Type"] = "application/json"
      res.body = calls == 1 ? body1 : body2
    end
    thr = Thread.new { server.start }
    port = server.config[:Port]

    begin
      KaraokeWeb::Ngrok.send(:remove_const, :API_URL)
      KaraokeWeb::Ngrok.const_set(:API_URL, "http://127.0.0.1:#{port}/api/tunnels")
      KaraokeWeb::Ngrok.instance_variable_set(:@cached_at, nil)
      KaraokeWeb::Ngrok.instance_variable_set(:@cached_url, nil)

      assert_equal "https://first.ngrok.io", KaraokeWeb::Ngrok.public_url
      # Within TTL: cached value, no second HTTP call.
      assert_equal "https://first.ngrok.io", KaraokeWeb::Ngrok.public_url
      assert_equal 1, calls, "cached value should prevent a second API call"
    ensure
      server.shutdown
      thr.join(1)
    end
  end
end
