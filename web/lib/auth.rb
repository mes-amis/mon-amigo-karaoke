module KaraokeWeb
  module Auth
    DEFAULT_PASSWORD = "Austin"

    def self.admin_password
      env = ENV["KARAOKE_ADMIN_PASSWORD"]
      (env.nil? || env.empty?) ? DEFAULT_PASSWORD : env
    end

    def self.configured?
      pw = admin_password
      !pw.nil? && !pw.empty?
    end

    def self.using_default?
      env = ENV["KARAOKE_ADMIN_PASSWORD"]
      env.nil? || env.empty?
    end

    def self.check(password)
      return false unless configured?
      # Constant-time compare so timing doesn't leak the password.
      a = admin_password.to_s.b
      b = password.to_s.b
      return false unless a.bytesize == b.bytesize
      Rack::Utils.secure_compare(a, b)
    end
  end
end
