module KaraokeWeb
  # Scans the on-disk karaoke output folder and reports MP4s that are ready
  # to perform. One MP4 per song; filename (without extension) is the title.
  module Library
    DEFAULT_ROOT = File.expand_path("~/Desktop/mon-amigo-karaoke")

    class << self
      def root
        @root ||= File.expand_path(ENV["KARAOKE_VIDEO_DIR"] || DEFAULT_ROOT)
      end

      attr_writer :root

      def ready_videos
        return [] unless Dir.exist?(root)
        Dir.glob(File.join(root, "*.mp4")).sort_by { |p| p.downcase }.map do |path|
          {
            title: File.basename(path, ".mp4"),
            video_path: path,
            size: File.size(path),
            mtime: File.mtime(path).to_i,
          }
        end
      end

      def video_exists?(title)
        File.file?(path_for(title))
      end

      def path_for(title)
        File.join(root, "#{safe_filename(title)}.mp4")
      end

      def safe_filename(name)
        name.gsub(/[<>:"\/\\|?*]/, "_").strip.then { |s| s.empty? ? "karaoke" : s }
      end
    end
  end
end
