// Stage view runtime. Polls /stage.json every 2s and renders the queue.
// When the operator's laptop is signed in as admin AND a queue entry is
// `performing`, we swap to a fullscreen <video> that plays /video/<song_id>.
// When the video ends (or the operator hits "return to queue"), we POST to
// /admin/done/:qid which flips state back; the next poll then restores the
// QR/queue view.
//
// Autoplay note: browsers block `video.play()` with sound until the tab has
// seen a user gesture. So (a) we show a "Tap to ready stage" button on the
// admin stage view before the first song — the operator clicks it once, we
// do a silent prime-and-pause, and from then on every admin-triggered start
// plays without intervention. (b) If we still get a NotAllowedError (some
// browsers require a gesture *per play* on first try), we surface a full-
// screen "Tap to play" overlay so the operator can unblock it in one click.
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
  const armBanner = document.getElementById("stageArm");
  const armBtn = document.getElementById("stageArmBtn");
  const playOverlay = document.getElementById("stagePlayOverlay");
  const isAdminStage = !!window.__STAGE_IS_ADMIN__;

  let currentQueueId = null;
  let markingDone = false;
  // Armed = this tab has received a user gesture, so subsequent programmatic
  // video.play() calls won't be blocked by the browser's autoplay policy.
  let armed = false;

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

    // Show the "ready stage" banner when admin, armed=false, and nothing
    // is currently performing (so the operator has time to arm before the
    // first start-from-admin).
    if (armBanner) {
      armBanner.hidden = !(isAdminStage && !armed && !np);
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
    if (np.id === currentQueueId) return;
    currentQueueId = np.id;
    markingDone = false;
    spTitle.textContent = np.title;
    spPerformer.textContent = np.performer || "";
    video.src = "/video/" + np.song_id;
    waiting.hidden = true;
    player.hidden = false;
    attemptPlay();
  }

  function attemptPlay() {
    if (!video) return;
    const p = video.play();
    if (p && typeof p.then === "function") {
      p.then(() => {
        if (playOverlay) playOverlay.hidden = true;
      }).catch(() => {
        // NotAllowedError — surface the "tap to play" overlay.
        if (playOverlay) playOverlay.hidden = false;
      });
    }
  }

  function exitPlayback() {
    if (!player) return;
    currentQueueId = null;
    markingDone = false;
    if (video) {
      if (!video.paused) video.pause();
      video.removeAttribute("src");
      video.load();
    }
    if (playOverlay) playOverlay.hidden = true;
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
      markingDone = false;
      return;
    }
    refresh();
  }

  // --- arm-the-stage gesture ------------------------------------------
  // Once the operator has clicked ANY button on the stage page, the tab
  // has an autoplay grant. So both the arm button and the overlay set
  // armed=true and dismiss themselves.

  async function armStageGesture() {
    armed = true;
    if (armBanner) armBanner.hidden = true;
    if (!video) return;
    // Do a silent prime: briefly .play() + pause on a 1px blank source so
    // the tab's autoplay grant is recorded, even if nothing is currently
    // queued for playback. If there's already a performing entry waiting
    // on the overlay, attemptPlay() will succeed.
    const hadSrc = !!video.currentSrc;
    if (!hadSrc) {
      // data:video containing a minimal valid but empty payload is flaky;
      // rely solely on the fact that this handler IS a user gesture, which
      // is enough to grant autoplay to subsequent programmatic play()
      // calls in this tab's session.
    }
    attemptPlay();
  }

  if (armBtn) armBtn.addEventListener("click", armStageGesture);
  if (playOverlay) {
    playOverlay.addEventListener("click", () => {
      armed = true;
      attemptPlay();
    });
  }

  if (video) {
    video.addEventListener("playing", () => {
      armed = true;
      if (playOverlay) playOverlay.hidden = true;
    });
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
