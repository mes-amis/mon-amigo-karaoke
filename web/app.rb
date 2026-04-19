require "sinatra/base"
require "sinatra/json"
require "json"
require "rqrcode"
require "securerandom"

require_relative "lib/db"
require_relative "lib/library"
require_relative "lib/ngrok"
require_relative "lib/auth"
require_relative "lib/itunes_search"
require_relative "lib/music_app"
require_relative "lib/pipeline"

module KaraokeWeb
  class App < Sinatra::Base
    helpers Sinatra::JSON

    # Pull every on-disk MP4 into the songs table as `ready`. Run at boot;
    # phase 2 will run it on-demand too as renders complete.
    def self.sync_library_once!
      Library.ready_videos.each do |v|
        DB.upsert_song(
          title: v[:title],
          video_path: v[:video_path],
          status: "ready",
        )
      end
    end

    configure do
      set :root, File.dirname(__FILE__)
      set :views, File.join(root, "views")
      set :public_folder, File.join(root, "public")
      set :show_exceptions, (environment == :development ? :after_handler : false)

      enable :sessions
      set :session_secret, ENV.fetch("KARAOKE_SESSION_SECRET") { SecureRandom.hex(32) }

      DB.migrate!
      sync_library_once!
    end

    configure :development do
      require "sinatra/reloader"
      register Sinatra::Reloader
      also_reload "lib/*.rb"
    end

    # ---------- helpers ----------
    helpers do
      def db
        DB.connection
      end

      def current_participant
        return nil unless session[:participant_id]
        @current_participant ||= db.get_first_row(
          "SELECT * FROM participants WHERE id = ?", [session[:participant_id]]
        )
      end

      def admin?
        session[:admin] == true
      end

      def require_admin!
        return if admin?
        redirect "/admin/login"
      end

      def queue_rows
        db.execute(<<~SQL)
          SELECT qe.id, qe.position, qe.state, qe.created_at,
                 p.id AS participant_id, p.name AS participant_name,
                 s.id AS song_id, s.title, s.artist, s.album, s.status AS song_status,
                 s.error, s.video_path
            FROM queue_entries qe
            JOIN participants p ON p.id = qe.participant_id
            JOIN songs s ON s.id = qe.song_id
           WHERE qe.state IN ('pending','performing')
           ORDER BY CASE qe.state WHEN 'performing' THEN 0 ELSE 1 END, qe.position ASC
        SQL
      end

      def queue_payload
        queue_rows.map do |r|
          {
            id: r["id"],
            song_id: r["song_id"],
            state: r["state"],
            position: r["position"],
            performer: r["participant_name"],
            title: r["title"],
            artist: r["artist"],
            album: r["album"],
            song_status: r["song_status"],
            ready: r["song_status"] == "ready",
          }
        end
      end

      def stage_payload
        {
          queue: queue_payload,
          now_playing: queue_payload.find { |q| q[:state] == "performing" },
          admin_required: Auth.configured?,
        }
      end

      def qr_svg(text)
        RQRCode::QRCode.new(text, level: :m).as_svg(
          offset: 0, color: "000",
          shape_rendering: "crispEdges",
          module_size: 6,
          standalone: true,
          use_path: true,
          svg_attributes: { class: "qr" },
        )
      end

      def h(str)
        Rack::Utils.escape_html(str.to_s)
      end
    end

    # ---------- participant-facing routes ----------

    get "/" do
      if current_participant
        @songs = db.execute(
          "SELECT id, title, artist, album, status FROM songs WHERE status = 'ready' ORDER BY title COLLATE NOCASE"
        )
        @my_picks = db.execute(<<~SQL, [current_participant["id"]])
          SELECT qe.id, qe.position, qe.state, s.title, s.artist, s.status AS song_status
            FROM queue_entries qe
            JOIN songs s ON s.id = qe.song_id
           WHERE qe.participant_id = ?
             AND qe.state IN ('pending','performing')
           ORDER BY qe.position ASC
        SQL
        erb :pick
      else
        erb :join
      end
    end

    post "/join" do
      name = params[:name].to_s.strip
      halt 400, "Name required" if name.empty?
      halt 400, "Name too long" if name.length > 40
      db.execute(
        "INSERT INTO participants(name) VALUES (?)", [name]
      )
      session[:participant_id] = db.last_insert_row_id
      redirect "/"
    end

    post "/signout" do
      session.delete(:participant_id)
      redirect "/"
    end

    post "/pick" do
      halt 401, "Join first" unless current_participant

      # One pick per participant in the live queue — finish the current
      # turn before picking again. Exception: if the existing open pick's
      # song is in `failed` state (download or render blew up), auto-skip
      # it so the participant doesn't dead-end.
      blocking = db.execute(<<~SQL, [current_participant["id"]])
        SELECT qe.id, qe.state, s.status AS song_status
          FROM queue_entries qe
          JOIN songs s ON s.id = qe.song_id
         WHERE qe.participant_id = ?
           AND qe.state IN ('pending','performing')
      SQL
      if blocking.any?
        all_failed = blocking.all? { |b| b["song_status"] == "failed" }
        performing = blocking.any? { |b| b["state"] == "performing" }
        if performing
          halt 400, "You're about to perform — admin needs to finish your current turn first."
        elsif all_failed
          ids = blocking.map { |b| b["id"] }
          placeholders = (["?"] * ids.length).join(",")
          db.execute(
            "UPDATE queue_entries SET state = 'skipped' WHERE id IN (#{placeholders})",
            ids
          )
        else
          halt 400, "Finish your current pick first"
        end
      end

      # Pick can reference an existing row by id OR describe a fresh song
      # from search results by (title, artist, album).
      song_id = params[:song_id].to_i
      if song_id <= 0
        title = params[:title].to_s.strip
        halt 400, "Missing song_id or title" if title.empty?
        artist = params[:artist].to_s.strip
        album = params[:album].to_s.strip
        # Optional: an audio file path (from filesystem-fallback search hits).
        # Only honor it if the file exists and lives under one of the
        # whitelisted music roots — never let a participant point /pick at
        # an arbitrary file on disk.
        path = params[:path].to_s
        audio_path = nil
        if !path.empty?
          real = (File.realpath(path) rescue nil)
          roots = MusicApp.fs_roots.map { |r| (File.realpath(r) rescue nil) }.compact
          if real && File.file?(real) && roots.any? { |r| real.start_with?(r + "/") }
            audio_path = real
          end
        end
        initial_status = Library.video_exists?(title) ? "ready" : "pending"
        song_id = DB.upsert_song(
          title: title,
          artist: artist.empty? ? nil : artist,
          album: album.empty? ? nil : album,
          audio_path: audio_path,
          status: initial_status,
        )
      end

      song = db.get_first_row("SELECT * FROM songs WHERE id = ?", [song_id])
      halt 404, "Unknown song" unless song

      pos = DB.next_position
      db.execute(
        "INSERT INTO queue_entries(participant_id, song_id, position) VALUES (?, ?, ?)",
        [current_participant["id"], song_id, pos]
      )

      # Fire off the download/render pipeline for anything not already ready.
      Pipeline.enqueue(song_id) unless song["status"] == "ready"

      if request.xhr?
        json ok: true, song_id: song_id
      else
        redirect "/"
      end
    end

    post "/unpick" do
      halt 401, "Join first" unless current_participant
      # Cancels any of the participant's pending entries (leaves `performing`
      # alone — the admin is driving that one). Mark as `skipped` rather than
      # hard-deleting so the history survives for the admin audit view.
      rows = db.execute(<<~SQL, [current_participant["id"]])
        SELECT id FROM queue_entries
         WHERE participant_id = ?
           AND state = 'pending'
      SQL
      ids = rows.map { |r| r["id"] }
      unless ids.empty?
        placeholders = (["?"] * ids.length).join(",")
        db.execute(
          "UPDATE queue_entries SET state = 'skipped' WHERE id IN (#{placeholders})",
          ids
        )
      end

      if request.xhr?
        json ok: true, cancelled: ids.length
      else
        redirect "/"
      end
    end

    get "/songs/search" do
      content_type :json
      q = params[:q].to_s.strip
      halt 400, { error: "missing q" }.to_json if q.empty?

      qlike = "%#{q.downcase}%"
      library_sql = <<~SQL
        SELECT id, title, artist, album, status
          FROM songs
         WHERE LOWER(title) LIKE ?
            OR LOWER(COALESCE(artist, '')) LIKE ?
            OR LOWER(COALESCE(album,  '')) LIKE ?
         ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END,
                  title COLLATE NOCASE
         LIMIT 12
      SQL
      # Tier 1: already-rendered MP4s (fastest path — zero wait).
      ready = db.execute(library_sql, [qlike, qlike, qlike]).map do |r|
        {
          id: r["id"], title: r["title"], artist: r["artist"], album: r["album"],
          status: r["status"],
          source: r["status"] == "ready" ? "library" : "preparing",
        }
      end

      # Tier 2: files already on disk.
      # Two sources that often complement each other:
      #   (a) Music.app's library playlist (AppleScript)
      #   (b) A filesystem walk of the standard iTunes/Music roots, which
      #       catches the legacy ~/Music/iTunes/... layout Music.app won't
      #       report on.
      local_hits = []
      paths_seen = {}
      (MusicApp.search_library(q, limit: 10) +
       MusicApp.search_filesystem(q, limit: 20)).each do |t|
        path = t[:path]
        next if path && paths_seen[path]
        paths_seen[path] = true if path
        local_hits << {
          title: t[:title], artist: t[:artist], album: t[:album],
          path: path, source: "local",
        }
      end
      local_hits = local_hits.first(15)

      # Tier 3: iTunes catalog only (needs download + render; 1-2 min total).
      catalog = ItunesSearch.search(q, limit: 10).map do |t|
        {
          title: t[:title], artist: t[:artist], album: t[:album],
          year: t[:year], source: "catalog",
        }
      end

      # Dedup across tiers by (title, artist). Each successive tier drops any
      # entry already represented in an earlier tier, so "already downloaded"
      # always wins over "download from iTunes".
      seen = {}
      key = ->(r) { [r[:title].to_s.downcase, r[:artist].to_s.downcase] }
      ready.each { |r| seen[key.call(r)] = true }
      local_hits.reject! { |r| seen[key.call(r)] }
      local_hits.each { |r| seen[key.call(r)] = true }
      catalog.reject! { |c| seen[key.call(c)] }

      json ready: ready, local: local_hits, catalog: catalog
    end

    post "/admin/retry/:song_id" do
      require_admin!
      song_id = params[:song_id].to_i
      song = db.get_first_row("SELECT * FROM songs WHERE id = ?", [song_id])
      halt 404 unless song
      db.execute(
        "UPDATE songs SET status = 'pending', error = NULL, updated_at = datetime('now') WHERE id = ?",
        [song_id]
      )
      Pipeline.enqueue(song_id)
      redirect "/admin"
    end

    # ---------- stage (big screen) ----------

    get "/me.json" do
      content_type :json
      halt 401, { error: "not joined" }.to_json unless current_participant
      rows = db.execute(<<~SQL, [current_participant["id"]])
        SELECT qe.id, qe.position, qe.state, s.title, s.artist, s.status AS song_status,
               s.error AS song_error
          FROM queue_entries qe
          JOIN songs s ON s.id = qe.song_id
         WHERE qe.participant_id = ?
           AND qe.state IN ('pending','performing')
         ORDER BY qe.position
      SQL
      json picks: rows.map { |r|
        {
          id: r["id"],
          position: r["position"],
          state: r["state"],
          title: r["title"],
          artist: r["artist"],
          song_status: r["song_status"],
          song_error: r["song_error"],
          ready: r["song_status"] == "ready",
        }
      }
    end

    get "/stage" do
      @tunnel_url = Ngrok.public_url
      @join_url = @tunnel_url || "http://#{request.host_with_port}/"
      @qr = qr_svg(@join_url)
      @stage = stage_payload
      erb :stage, layout: :stage_layout
    end

    get "/stage.json" do
      content_type :json
      stage_payload.to_json
    end

    # ---------- admin ----------

    get "/admin/login" do
      unless Auth.configured?
        halt 503, "Set KARAOKE_ADMIN_PASSWORD before accessing /admin."
      end
      erb :admin_login
    end

    post "/admin/login" do
      if Auth.check(params[:password])
        session[:admin] = true
        redirect "/admin"
      else
        @error = "Wrong password"
        status 401
        erb :admin_login
      end
    end

    post "/admin/logout" do
      session.delete(:admin)
      redirect "/"
    end

    get "/admin" do
      require_admin!
      @queue = queue_rows
      @songs = db.execute("SELECT id, title, artist, album, status FROM songs ORDER BY title COLLATE NOCASE")
      @participants = db.execute("SELECT id, name FROM participants ORDER BY id DESC")
      erb :admin
    end

    post "/admin/start/:id" do
      require_admin!
      id = params[:id].to_i
      # A performing row stays 'performing' until explicitly finished/skipped.
      # Anyone currently performing is pushed back to pending (at the top).
      db.transaction do
        db.execute("UPDATE queue_entries SET state = 'pending' WHERE state = 'performing'")
        db.execute("UPDATE queue_entries SET state = 'performing' WHERE id = ?", [id])
      end
      redirect "/admin"
    end

    post "/admin/start-next" do
      require_admin!
      row = db.get_first_row(
        "SELECT id FROM queue_entries WHERE state = 'pending' ORDER BY position ASC LIMIT 1"
      )
      if row
        db.transaction do
          db.execute("UPDATE queue_entries SET state = 'pending' WHERE state = 'performing'")
          db.execute("UPDATE queue_entries SET state = 'performing' WHERE id = ?", [row["id"]])
        end
      end
      back_to = request.referer && request.referer.include?("/stage") ? "/stage" : "/admin"
      redirect back_to
    end

    post "/admin/done/:id" do
      require_admin!
      id = params[:id].to_i
      db.execute("UPDATE queue_entries SET state = 'done' WHERE id = ?", [id])
      redirect "/admin"
    end

    post "/admin/skip/:id" do
      require_admin!
      id = params[:id].to_i
      db.execute("UPDATE queue_entries SET state = 'skipped' WHERE id = ?", [id])
      redirect "/admin"
    end

    post "/admin/remove/:id" do
      require_admin!
      id = params[:id].to_i
      db.execute("DELETE FROM queue_entries WHERE id = ?", [id])
      redirect "/admin"
    end

    post "/admin/reorder" do
      require_admin!
      ids = Array(params[:order]).map(&:to_i)
      db.transaction do
        ids.each_with_index do |qid, i|
          db.execute("UPDATE queue_entries SET position = ? WHERE id = ?", [i + 1, qid])
        end
      end
      json ok: true
    end

    post "/admin/rescan" do
      require_admin!
      App.sync_library_once!
      MusicApp.invalidate_fs_index!
      redirect "/admin"
    end

    post "/admin/delete-song/:song_id" do
      require_admin!
      song_id = params[:song_id].to_i
      # ON DELETE CASCADE on queue_entries.song_id drops any live queue rows
      # pointing at this song, so we don't orphan anything.
      db.execute("DELETE FROM songs WHERE id = ?", [song_id])
      redirect "/admin"
    end

    post "/admin/fix/:song_id" do
      require_admin!
      song_id = params[:song_id].to_i
      song = db.get_first_row("SELECT * FROM songs WHERE id = ?", [song_id])
      halt 404 unless song

      raw = params[:path].to_s.strip
      if raw.empty?
        halt 400, "Please provide a path to the audio file."
      end

      # Admins often paste paths in shell form — with backslash escapes
      # for spaces/parens/apostrophes and sometimes wrapped in quotes.
      # Normalize so the posted string matches how a human reads the path.
      cleaned = raw
      if (cleaned.start_with?('"') && cleaned.end_with?('"')) ||
         (cleaned.start_with?("'") && cleaned.end_with?("'"))
        cleaned = cleaned[1..-2]
      end
      cleaned = cleaned.gsub(/\\(.)/, '\1')

      # Tilde-expand and canonicalize. We trust the admin here (they have
      # the password) but still sanity-check that it's a real, readable
      # audio file before we kick off a pipeline run.
      expanded = File.expand_path(cleaned)
      real = (File.realpath(expanded) rescue nil)
      unless real && File.file?(real)
        halt 400, "No file at #{cleaned}"
      end
      ext = File.extname(real).downcase
      unless MusicApp::FS_AUDIO_EXTS.include?(ext)
        halt 400, "Unsupported audio extension: #{ext}"
      end

      db.execute(
        "UPDATE songs SET audio_path = ?, status = 'pending', error = NULL, " \
        "updated_at = datetime('now') WHERE id = ?",
        [real, song_id]
      )
      Pipeline.enqueue(song_id)
      redirect "/admin"
    end

    # Expose a ready MP4 for in-browser playback on the stage machine.
    # Path is constrained to the Library root so we never serve arbitrary files.
    get "/video/:id" do
      song = db.get_first_row("SELECT * FROM songs WHERE id = ?", [params[:id].to_i])
      halt 404 unless song && song["video_path"]
      real = File.realpath(song["video_path"]) rescue nil
      halt 404 unless real && real.start_with?(File.realpath(Library.root) + "/")
      send_file real, type: "video/mp4", disposition: "inline"
    end
  end
end
