// Participant page: instant-filter the song list as they type.
(function () {
  const input = document.getElementById("songFilter");
  const list = document.getElementById("songList");
  if (!input || !list) return;
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    for (const row of list.children) {
      const hay = row.getAttribute("data-search") || "";
      row.style.display = hay.includes(q) ? "" : "none";
    }
  });
})();
