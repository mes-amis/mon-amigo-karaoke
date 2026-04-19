require_relative "test_helper"

class LibraryTest < Minitest::Test
  include KaraokeTestIsolation

  def test_ready_videos_empty_when_dir_empty
    assert_equal [], KaraokeWeb::Library.ready_videos
  end

  def test_ready_videos_lists_mp4s_only
    make_video("Ocean Man")
    make_video("Birdhouse in Your Soul")
    FileUtils.touch(File.join(@video_root, "notes.txt"))
    FileUtils.touch(File.join(@video_root, "half.wav"))

    titles = KaraokeWeb::Library.ready_videos.map { |v| v[:title] }
    assert_equal ["Birdhouse in Your Soul", "Ocean Man"], titles
  end

  def test_ready_videos_sorted_case_insensitive
    make_video("zebra")
    make_video("Alpha")
    make_video("mango")
    titles = KaraokeWeb::Library.ready_videos.map { |v| v[:title] }
    assert_equal ["Alpha", "mango", "zebra"], titles
  end

  def test_video_exists_and_path_for_round_trip
    make_video("Doctor Worm")
    assert KaraokeWeb::Library.video_exists?("Doctor Worm")
    refute KaraokeWeb::Library.video_exists?("Never Rendered")
    assert_equal File.join(@video_root, "Doctor Worm.mp4"),
                 KaraokeWeb::Library.path_for("Doctor Worm")
  end

  def test_safe_filename_strips_problem_chars
    assert_equal "a_b_c_d",
                 KaraokeWeb::Library.safe_filename("a/b:c?d")
    assert_equal "karaoke", KaraokeWeb::Library.safe_filename("")
    assert_equal "karaoke", KaraokeWeb::Library.safe_filename("   ")
  end
end
