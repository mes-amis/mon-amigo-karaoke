// Stage view runtime. Polls /stage.json every 2s and renders the queue.
// When the operator's laptop is signed in as admin AND a queue entry is
// `performing`, we swap to a fullscreen <video> that plays /video/<song_id>.
// When the video ends (or the operator hits "return to queue"), we POST to
// /admin/done/:qid which flips state back; the next poll then restores the
// QR/queue view.
(function () {
  const waiting = document.getElementById("stageWaiting");
  const player = document.getElementById("stagePlayer");
  const video = document.getElementById("stageVideo");
  const spTitle = document.getElementById("spTitle");
  const spPerformer = document.getElementById("spPerformer");
  const npTitle = document.getElementById("npTitle");
  const npPerformer = document.getElementById("npPerformer");
  const queueEl = document.getElementById("queue");
  const startBtn = document.getElementById("stageStartBtn");
  const exitBtn = document.getElementById("spExit");
  const isAdminStage = !!window.__STAGE_IS_ADMIN__;

  // Track the queue-entry id we're currently playing so we only reload the
  // <video> src when it actually changes (avoids restarting mid-song on
  // every 2s poll).
  let currentQueueId = null;
  // Track whether we've already asked the server to mark this one done, so
  // a slow server round-trip doesn't cause a duplicate POST.
  let markingDone = false;

  async function refresh() {
    try {
      const res = await fetch("/stage.json", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      render(data);
    } catch (err) {
      // transient — try again next tick
    }
  }

  function render(data) {
    const np = data.now_playing;
    renderQueuePanel(data, np);

    if (isAdminStage && np && player && video) {
      enterPlayback(np);
    } else {
      exitPlayback();
    }
  }

  function renderQueuePanel(data, np) {
    if (!npTitle) return;
    if (np) {
      npTitle.textContent = np.title;
      npPerformer.textContent = np.performer ? "sung by " + np.performer : "";
    } else {
      npTitle.textContent = "—";
      npPerformer.textContent = "waiting for the next singer";
    }

    const upcoming = data.queue.filter((q) => q.state === "pending");
    queueEl.innerHTML = "";
    if (upcoming.length === 0) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "queue is empty — scan the QR to join";
      queueEl.appendChild(li);
      if (startBtn) startBtn.disabled = true;
      return;
    }
    if (startBtn) startBtn.disabled = !!np;
    upcoming.forEach((q, i) => {
      const li = document.createElement("li");
      const pos = document.createElement("span");
      pos.className = "qpos";
      pos.textContent = String(i + 1).padStart(2, "0");
      const performer = document.createElement("span");
      performer.className = "qperformer";
      performer.textContent = q.performer;
      const title = document.createElement("span");
      title.className = "qtitle";
      title.textContent = q.title;
      li.append(pos, performer, title);
      if (!q.ready) {
        const warn = document.createElement("span");
        warn.className = "qnotready";
        warn.textContent = q.song_status;
        li.append(warn);
      }
      queueEl.appendChild(li);
    });
  }

  function enterPlayback(np) {
    if (np.id === currentQueueId) return; // already playing this one
    currentQueueId = np.id;
    markingDone = false;
    spTitle.textContent = np.title;
    spPerformer.textContent = np.performer || "";
    video.src = "/video/" + np.song_id;
    waiting.hidden = true;
    player.hidden = false;
    // autoplay can fail (Safari w/o user gesture); controls stay visible
    // so the operator can hit play manually.
    video.play().catch(() => {});
  }

  function exitPlayback() {
    if (!player) return;
    currentQueueId = null;
    markingDone = false;
    if (!video.paused) video.pause();
    video.removeAttribute("src");
    video.load();
    player.hidden = true;
    waiting.hidden = false;
  }

  async function markDone() {
    if (!currentQueueId || markingDone) return;
    markingDone = true;
    const qid = currentQueueId;
    try {
      await fetch("/admin/done/" + qid, {
        method: "POST",
        credentials: "include",
      });
    } catch (err) {
      // If the POST fails we'll retry on the next 'ended' / click event.
      markingDone = false;
      return;
    }
    // Force an immediate re-poll so the UI flips without a 2s lag.
    refresh();
  }

  if (video) {
    video.addEventListener("ended", markDone);
    video.addEventListener("error", (e) => {
      console.warn("stage video error", e);
    });
  }
  if (exitBtn) {
    exitBtn.addEventListener("click", markDone);
  }

  refresh();
  setInterval(refresh, 2000);
})();
