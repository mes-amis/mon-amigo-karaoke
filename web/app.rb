require "sinatra/base"
require "sinatra/json"
require "json"
require "rqrcode"
require "securerandom"

require_relative "lib/db"
require_relative "lib/library"
require_relative "lib/ngrok"
require_relative "lib/auth"

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
                 s.video_path
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
      song_id = params[:song_id].to_i
      halt 400, "Missing song_id" if song_id <= 0
      song = db.get_first_row("SELECT * FROM songs WHERE id = ?", [song_id])
      halt 404, "Unknown song" unless song

      # Don't let one participant stack 5 picks before everyone else gets one.
      # Admins can still add via /admin.
      open_picks = db.get_first_row(<<~SQL, [current_participant["id"]])
        SELECT COUNT(*) AS n FROM queue_entries
         WHERE participant_id = ? AND state IN ('pending','performing')
      SQL
      halt 400, "Finish your current pick first" if open_picks["n"].to_i >= 1

      pos = DB.next_position
      db.execute(
        "INSERT INTO queue_entries(participant_id, song_id, position) VALUES (?, ?, ?)",
        [current_participant["id"], song_id, pos]
      )
      redirect "/"
    end

    # ---------- stage (big screen) ----------

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
