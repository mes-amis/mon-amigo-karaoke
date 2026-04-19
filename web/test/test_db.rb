require_relative "test_helper"

class DBTest < Minitest::Test
  include KaraokeTestIsolation

  def test_migrate_is_idempotent
    KaraokeWeb::DB.migrate!
    KaraokeWeb::DB.migrate!
    tables = KaraokeWeb::DB.connection
      .execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
      .map { |r| r["name"] }
    assert_includes tables, "participants"
    assert_includes tables, "songs"
    assert_includes tables, "queue_entries"
  end

  def test_upsert_song_inserts_then_updates
    KaraokeWeb::DB.migrate!
    id = KaraokeWeb::DB.upsert_song(title: "Ocean Man", status: "ready")
    again = KaraokeWeb::DB.upsert_song(
      title: "Ocean Man", artist: "Ween", status: "ready"
    )
    assert_equal id, again, "upsert should update the existing row, not create a new one"

    row = KaraokeWeb::DB.connection.get_first_row(
      "SELECT * FROM songs WHERE id = ?", [id]
    )
    assert_equal "Ween", row["artist"]
    assert_equal "ready", row["status"]
  end

  def test_upsert_song_preserves_existing_fields_on_partial_update
    KaraokeWeb::DB.migrate!
    id = KaraokeWeb::DB.upsert_song(title: "x", artist: "a", album: "b")
    KaraokeWeb::DB.upsert_song(title: "x", status: "rendering") # no artist/album
    row = KaraokeWeb::DB.connection.get_first_row("SELECT * FROM songs WHERE id = ?", [id])
    assert_equal "a", row["artist"]
    assert_equal "b", row["album"]
    assert_equal "rendering", row["status"]
  end

  def test_next_position_counts_only_live_rows
    KaraokeWeb::DB.migrate!
    c = KaraokeWeb::DB.connection
    sid = KaraokeWeb::DB.upsert_song(title: "s")
    c.execute("INSERT INTO participants(name) VALUES ('a')")
    pa = c.last_insert_row_id
    c.execute("INSERT INTO participants(name) VALUES ('b')")
    pb = c.last_insert_row_id

    assert_equal 1, KaraokeWeb::DB.next_position

    c.execute("INSERT INTO queue_entries(participant_id, song_id, position, state) VALUES (?, ?, 1, 'pending')", [pa, sid])
    assert_equal 2, KaraokeWeb::DB.next_position

    c.execute("INSERT INTO queue_entries(participant_id, song_id, position, state) VALUES (?, ?, 2, 'pending')", [pb, sid])
    assert_equal 3, KaraokeWeb::DB.next_position

    # Done/skipped rows shouldn't influence next_position.
    c.execute("UPDATE queue_entries SET state = 'done' WHERE position = 1")
    assert_equal 3, KaraokeWeb::DB.next_position
  end
end
