require_relative "test_helper"
require_relative "../lib/auth"

class AuthTest < Minitest::Test
  def setup
    @saved = ENV["KARAOKE_ADMIN_PASSWORD"]
  end

  def teardown
    @saved.nil? ? ENV.delete("KARAOKE_ADMIN_PASSWORD") : ENV["KARAOKE_ADMIN_PASSWORD"] = @saved
  end

  def test_defaults_to_austin_when_env_unset
    ENV.delete("KARAOKE_ADMIN_PASSWORD")
    assert_equal "Austin", KaraokeWeb::Auth.admin_password
    assert KaraokeWeb::Auth.configured?
    assert KaraokeWeb::Auth.using_default?
  end

  def test_defaults_when_env_empty_string
    ENV["KARAOKE_ADMIN_PASSWORD"] = ""
    assert_equal "Austin", KaraokeWeb::Auth.admin_password
    assert KaraokeWeb::Auth.using_default?
  end

  def test_env_override_wins
    ENV["KARAOKE_ADMIN_PASSWORD"] = "correct horse battery staple"
    assert_equal "correct horse battery staple", KaraokeWeb::Auth.admin_password
    refute KaraokeWeb::Auth.using_default?
  end

  def test_check_accepts_correct_password
    ENV["KARAOKE_ADMIN_PASSWORD"] = "swordfish"
    assert KaraokeWeb::Auth.check("swordfish")
  end

  def test_check_rejects_wrong_password
    ENV["KARAOKE_ADMIN_PASSWORD"] = "swordfish"
    refute KaraokeWeb::Auth.check("guess")
    refute KaraokeWeb::Auth.check("")
    refute KaraokeWeb::Auth.check(nil)
  end

  def test_check_is_length_safe
    ENV["KARAOKE_ADMIN_PASSWORD"] = "abcdef"
    # Same prefix, different length — must be rejected without raising.
    refute KaraokeWeb::Auth.check("abc")
  end

  def test_default_accepts_austin
    ENV.delete("KARAOKE_ADMIN_PASSWORD")
    assert KaraokeWeb::Auth.check("Austin")
    refute KaraokeWeb::Auth.check("austin"), "password check is case-sensitive"
  end
end
