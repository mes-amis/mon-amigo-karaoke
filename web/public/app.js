// Participant page runtime:
//   1. Debounced /songs/search.json search (library + iTunes catalog).
//   2. POST /pick via fetch so we can stay on-page and show the result.
//   3. Poll /me.json every 2s to surface downloading/rendering status on
//      the participant's own pick card.
(function () {
  const searchInput = document.getElementById("songSearch");
  const results = document.getElementById("searchResults");
  const searchCard = document.getElementById("searchCard");
  const picksCard = document.getElementById("myPicksCard");
  const picksList = document.getElementById("myPicksList");

  // --- search --------------------------------------------------------

  if (searchInput && results) {
    let timer = null;
    let activeRequest = 0;

    searchInput.addEventListener("input", () => {
      const q = searchInput.value.trim();
      clearTimeout(timer);
      if (q.length < 2) {
        // Too short — leave the server-rendered "Ready to sing" list visible.
        return;
      }
      timer = setTimeout(() => runSearch(q), 250);
    });

    async function runSearch(q) {
      const reqId = ++activeRequest;
      results.classList.add("searching");
      try {
        const res = await fetch(
          "/songs/search?q=" + encodeURIComponent(q),
          { cache: "no-store" }
        );
        if (reqId !== activeRequest) return; // superseded by newer keystroke
        if (!res.ok) {
          results.innerHTML = "<p class=\"muted\">Search failed.</p>";
          return;
        }
        const data = await res.json();
        renderSearchResults(data);
      } catch (err) {
        if (reqId !== activeRequest) return;
        results.innerHTML = "<p class=\"muted\">Offline?</p>";
      } finally {
        if (reqId === activeRequest) results.classList.remove("searching");
      }
    }

    function renderSearchResults(data) {
      results.innerHTML = "";
      const ready = data.ready || [];
      const local = data.local || [];
      const catalog = data.catalog || [];
      const fast = ready.concat(local); // ready MP4s + Music.app local library
      if (fast.length === 0 && catalog.length === 0) {
        const p = document.createElement("p");
        p.className = "muted";
        p.textContent = "No matches — try a different search.";
        results.appendChild(p);
        return;
      }
      if (fast.length) {
        results.appendChild(sectionLabel("Ready / downloaded (fastest)"));
        results.appendChild(resultList(fast, /*fromCatalog*/ false));
      }
      if (catalog.length) {
        results.appendChild(sectionLabel("From iTunes catalog (download first)"));
        results.appendChild(resultList(catalog, /*fromCatalog*/ true));
      }
    }

    function sectionLabel(text) {
      const div = document.createElement("div");
      div.className = "search-section-label";
      div.textContent = text;
      return div;
    }

    function resultList(items, fromCatalog) {
      const ul = document.createElement("ul");
      ul.className = "song-list";
      items.forEach((item) => {
        const li = document.createElement("li");
        li.className = "song-row";

        const meta = document.createElement("div");
        meta.className = "song-meta";
        const title = document.createElement("div");
        title.className = "song-title";
        title.textContent = item.title;
        meta.appendChild(title);
        const credit = [item.artist, item.album].filter(Boolean).join(" — ");
        if (credit) {
          const c = document.createElement("div");
          c.className = "muted";
          c.textContent = credit;
          meta.appendChild(c);
        }
        let hint = null;
        if (fromCatalog) {
          hint = "~1–2 min — Music.app will download it first";
        } else if (item.source === "library") {
          hint = "ready to sing now";
        } else if (item.source === "local") {
          hint = "in your Apple Music — ~30–60s to prep";
        } else if (item.source === "preparing") {
          hint = "preparing — " + item.status;
        }
        if (hint) {
          const badge = document.createElement("div");
          badge.className = "muted small source-" + (item.source || "catalog");
          badge.textContent = hint;
          meta.appendChild(badge);
        }
        li.appendChild(meta);

        const btn = document.createElement("button");
        btn.className = "primary small";
        btn.type = "button";
        btn.textContent =
          fromCatalog ? "Pick + download"
          : item.source === "local" ? "Pick + prep"
          : "Pick";
        btn.addEventListener("click", () => pick(item, btn));
        li.appendChild(btn);

        ul.appendChild(li);
      });
      return ul;
    }
  }

  // Wire the server-rendered "Ready to sing" forms to use fetch + reveal
  // the picks panel without a reload, so participants don't lose scroll.
  document.querySelectorAll("form.inline[action='/pick']").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = form.querySelector("button");
      const songId = form.querySelector("input[name=song_id]").value;
      pick({ song_id: songId }, btn);
    });
  });

  // Server-rendered "Cancel" / "Pick a different song" buttons on the
  // initial picks panel. JS-rendered rows attach their own listener below.
  document.querySelectorAll(".pick-cancel").forEach((btn) => {
    btn.addEventListener("click", () => cancelPick(btn));
  });

  async function cancelPick(btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "cancelling...";
    }
    try {
      await fetch("/unpick", {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "include",
      });
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.cancelLabel || "Cancel";
      }
      return;
    }
    if (picksCard) picksCard.hidden = true;
    if (searchCard) searchCard.hidden = false;
    refreshMyPicks();
    const s = document.getElementById("songSearch");
    if (s) s.focus();
  }

  async function pick(item, btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "picking...";
    }
    const body = new URLSearchParams();
    if (item.song_id) body.set("song_id", item.song_id);
    if (item.id) body.set("song_id", item.id);
    if (item.title) body.set("title", item.title);
    if (item.artist) body.set("artist", item.artist);
    if (item.album) body.set("album", item.album);

    const res = await fetch("/pick", {
      method: "POST",
      body,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "include",
    });
    if (!res.ok) {
      const msg = await res.text();
      alert(msg || "Couldn't pick that song.");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Pick";
      }
      return;
    }
    if (searchCard) searchCard.hidden = true;
    if (picksCard) picksCard.hidden = false;
    refreshMyPicks();
  }

  // --- my-picks live status -----------------------------------------

  async function refreshMyPicks() {
    if (!picksList) return;
    try {
      const res = await fetch("/me.json", {
        cache: "no-store",
        credentials: "include",
      });
      if (!res.ok) return;
      const data = await res.json();
      renderMyPicks(data.picks || []);
    } catch (err) {
      // transient
    }
  }

  function renderMyPicks(picks) {
    if (!picksCard || !picksList) return;
    if (picks.length === 0) {
      picksCard.hidden = true;
      if (searchCard) searchCard.hidden = false;
      picksList.innerHTML = "";
      return;
    }
    picksCard.hidden = false;
    if (searchCard) searchCard.hidden = true;

    picksList.innerHTML = "";
    picks.forEach((p) => {
      const li = document.createElement("li");
      li.className = "pick-row";
      li.dataset.pickId = p.id;

      const meta = document.createElement("div");
      meta.className = "pick-meta";
      const title = document.createElement("div");
      title.className = "pick-title";
      title.textContent = p.title;
      meta.appendChild(title);
      if (p.artist) {
        const c = document.createElement("div");
        c.className = "muted";
        c.textContent = p.artist;
        meta.appendChild(c);
      }
      if (p.song_error) {
        const err = document.createElement("div");
        err.className = "pick-error muted small";
        err.textContent = p.song_error;
        meta.appendChild(err);
      }
      li.appendChild(meta);

      const status = document.createElement("div");
      status.className = "pick-status";
      const stateTag = document.createElement("span");
      stateTag.className =
        "tag " + (p.state === "performing" ? "performing" : "pending");
      stateTag.textContent =
        p.state === "performing" ? "now performing" : "position #" + p.position;
      status.appendChild(stateTag);
      const songTag = document.createElement("span");
      songTag.className = "tag song-" + p.song_status;
      songTag.textContent = p.song_status;
      status.appendChild(songTag);
      if (p.state !== "performing") {
        const cancel = document.createElement("button");
        cancel.className = "link small pick-cancel";
        cancel.type = "button";
        cancel.textContent =
          p.song_status === "failed" ? "Pick a different song" : "Cancel";
        cancel.dataset.cancelLabel = cancel.textContent;
        cancel.addEventListener("click", () => cancelPick(cancel));
        status.appendChild(cancel);
      }
      li.appendChild(status);

      picksList.appendChild(li);
    });

    // If the only open pick is failed, keep search visible so the
    // participant can always get out of the dead end.
    if (searchCard) {
      const allFailed = picks.length > 0 && picks.every((p) => p.song_status === "failed");
      const anyPerformingOrPending = picks.some((p) =>
        p.state === "performing" || (p.song_status !== "failed" && p.state === "pending")
      );
      searchCard.hidden = anyPerformingOrPending && !allFailed;
    }
  }

  if (picksCard) {
    refreshMyPicks();
    setInterval(refreshMyPicks, 2000);
  }
})();
