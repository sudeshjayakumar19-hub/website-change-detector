/* ==========================================================================
   Website Change Detector — Version History & Diff Viewer Controller
   ========================================================================== */

(() => {
  "use strict";

  const siteId = Number(document.body.getAttribute("data-site-id"));
  let snapshots = [];
  let currentDiffMode = "side-by-side";
  let lastDiffData = null;

  // DOM Elements
  const historyStatusBadge = document.getElementById("historyStatusBadge");
  const historyConfigTags = document.getElementById("historyConfigTags");
  const metaInterval = document.getElementById("metaInterval");
  const metaLastChecked = document.getElementById("metaLastChecked");
  const metaLastChanged = document.getElementById("metaLastChanged");
  const metaSnapshotCount = document.getElementById("metaSnapshotCount");
  const historyCheckNowBtn = document.getElementById("historyCheckNowBtn");

  const diffControlsBar = document.getElementById("diffControlsBar");
  const fromVersionSelect = document.getElementById("fromVersionSelect");
  const toVersionSelect = document.getElementById("toVersionSelect");
  const compareVersionsBtn = document.getElementById("compareVersionsBtn");

  const diffStatsStrip = document.getElementById("diffStatsStrip");
  const diffAddedPill = document.getElementById("diffAddedPill");
  const diffRemovedPill = document.getElementById("diffRemovedPill");
  const diffSimilarityBadge = document.getElementById("diffSimilarityBadge");
  const diffOneLineSummary = document.getElementById("diffOneLineSummary");

  const diffEmptyState = document.getElementById("diffEmptyState");
  const diffNoChangesBanner = document.getElementById("diffNoChangesBanner");

  const sideBySideView = document.getElementById("sideBySideView");
  const leftDiffHeader = document.getElementById("leftDiffHeader");
  const rightDiffHeader = document.getElementById("rightDiffHeader");
  const sideBySideTableBody = document.getElementById("sideBySideTableBody");

  const unifiedDiffView = document.getElementById("unifiedDiffView");
  const unifiedDiffTableBody = document.getElementById("unifiedDiffTableBody");

  const beforeAfterView = document.getElementById("beforeAfterView");
  const beforePaneTitle = document.getElementById("beforePaneTitle");
  const beforePaneMeta = document.getElementById("beforePaneMeta");
  const beforePaneText = document.getElementById("beforePaneText");
  const afterPaneTitle = document.getElementById("afterPaneTitle");
  const afterPaneMeta = document.getElementById("afterPaneMeta");
  const afterPaneText = document.getElementById("afterPaneText");

  const snapshotsTimelineList = document.getElementById("snapshotsTimelineList");
  const checkRunsList = document.getElementById("checkRunsList");
  const toastContainer = document.getElementById("toastContainer");

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    const div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
  }

  function formatDateTime(isoString) {
    if (!isoString) return "Never";
    try {
      const d = new Date(isoString);
      if (Number.isNaN(d.getTime())) return "Never";
      return d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return "Never";
    }
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => toast.remove(), 4500);
  }

  function updateStatusBadge(status) {
    const st = (status || "pending").toLowerCase();
    historyStatusBadge.className = "badge";
    if (st === "changed") {
      historyStatusBadge.classList.add("badge-changed");
      historyStatusBadge.textContent = "Changed";
    } else if (st === "unchanged") {
      historyStatusBadge.classList.add("badge-unchanged");
      historyStatusBadge.textContent = "Unchanged";
    } else if (st === "error") {
      historyStatusBadge.classList.add("badge-error");
      historyStatusBadge.textContent = "Check Failed";
    } else {
      historyStatusBadge.classList.add("badge-pending");
      historyStatusBadge.textContent = "Pending";
    }
  }

  function renderDiffViews(diffData) {
    lastDiffData = diffData;
    if (!diffData) return;

    const fromSnap = diffData.from_snapshot;
    const toSnap = diffData.to_snapshot;
    const stats = diffData.stats;

    diffStatsStrip.style.display = "flex";
    diffAddedPill.textContent = `+${stats.added_lines} added line(s)`;
    diffRemovedPill.textContent = `-${stats.removed_lines} removed line(s)`;
    diffSimilarityBadge.textContent = `${(stats.similarity_ratio * 100).toFixed(1)}% similarity`;
    diffOneLineSummary.textContent = stats.summary;

    diffNoChangesBanner.style.display = stats.has_meaningful_changes ? "none" : "block";

    // 1. Populate Side-by-Side Table
    leftDiffHeader.textContent = `v${fromSnap.version_number} (${formatDateTime(fromSnap.created_at)}) — ${fromSnap.content_hash.slice(0, 12)}...`;
    rightDiffHeader.textContent = `v${toSnap.version_number} (${formatDateTime(toSnap.created_at)}) — ${toSnap.content_hash.slice(0, 12)}...`;

    sideBySideTableBody.innerHTML = diffData.side_by_side
      .map((row) => {
        const leftClass =
          row.left_type === "delete"
            ? "diff-cell-delete"
            : row.left_type === "empty"
            ? "diff-cell-empty"
            : "";
        const rightClass =
          row.right_type === "insert"
            ? "diff-cell-insert"
            : row.right_type === "empty"
            ? "diff-cell-empty"
            : "";

        const leftPrefix = row.left_type === "delete" ? "- " : "  ";
        const rightPrefix = row.right_type === "insert" ? "+ " : "  ";

        return `
          <tr>
            <td class="diff-line-num">${row.left_line_no !== null ? row.left_line_no : ""}</td>
            <td class="${leftClass}">${row.left_type === "empty" ? "" : escapeHtml(leftPrefix + row.left_text)}</td>
            <td class="diff-line-num">${row.right_line_no !== null ? row.right_line_no : ""}</td>
            <td class="${rightClass}">${row.right_type === "empty" ? "" : escapeHtml(rightPrefix + row.right_text)}</td>
          </tr>
        `;
      })
      .join("");

    // 2. Populate Unified Diff Table
    if (!diffData.unified_diff || diffData.unified_diff.length === 0) {
      unifiedDiffTableBody.innerHTML = `
        <tr>
          <td colspan="3" style="text-align: center; padding: 1.5rem; color: var(--text-muted);">
            No line differences between v${fromSnap.version_number} and v${toSnap.version_number}.
          </td>
        </tr>
      `;
    } else {
      unifiedDiffTableBody.innerHTML = diffData.unified_diff
        .map((uLine) => {
          let rowClass = "";
          let prefix = " ";
          if (uLine.line_type === "added") {
            rowClass = "diff-cell-insert";
            prefix = "+";
          } else if (uLine.line_type === "removed") {
            rowClass = "diff-cell-delete";
            prefix = "-";
          } else if (uLine.line_type === "header") {
            rowClass = "diff-cell-empty";
            prefix = "";
          }
          return `
            <tr>
              <td class="diff-line-num">${uLine.old_line_no !== null ? uLine.old_line_no : ""}</td>
              <td class="diff-line-num">${uLine.new_line_no !== null ? uLine.new_line_no : ""}</td>
              <td class="${rowClass}">${escapeHtml(prefix + uLine.content)}</td>
            </tr>
          `;
        })
        .join("");
    }

    // 3. Populate Visual Before & After Panes
    beforePaneTitle.textContent = `Before — Version #${fromSnap.version_number} (${formatDateTime(fromSnap.created_at)})`;
    beforePaneMeta.textContent = `${fromSnap.word_count} words • ${fromSnap.char_count} chars`;
    beforePaneText.textContent = fromSnap.extracted_text || "(Empty)";

    afterPaneTitle.textContent = `After — Version #${toSnap.version_number} (${formatDateTime(toSnap.created_at)})`;
    afterPaneMeta.textContent = `${toSnap.word_count} words • ${toSnap.char_count} chars`;
    afterPaneText.textContent = toSnap.extracted_text || "(Empty)";

    applyActiveDiffTab();
  }

  function applyActiveDiffTab() {
    if (!lastDiffData) return;
    sideBySideView.style.display = currentDiffMode === "side-by-side" ? "block" : "none";
    unifiedDiffView.style.display = currentDiffMode === "unified" ? "block" : "none";
    beforeAfterView.style.display = currentDiffMode === "before-after" ? "grid" : "none";
  }

  async function loadDiffForPair(fromId, toId) {
    try {
      const params = new URLSearchParams();
      if (fromId) params.set("from_id", String(fromId));
      if (toId) params.set("to_id", String(toId));
      const res = await fetch(`/api/sites/${siteId}/diff?${params.toString()}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to load diff comparison.");
      }
      const diffData = await res.json();
      renderDiffViews(diffData);
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function renderSnapshotsTimeline(snaps) {
    if (!snaps || snaps.length === 0) {
      snapshotsTimelineList.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 1.25rem;">
          No version snapshots saved yet.
        </div>
      `;
      return;
    }

    snapshotsTimelineList.innerHTML = snaps
      .map((snap, idx) => {
        const prevSnap = snaps[idx + 1] || null;
        const compareBtnHtml = prevSnap
          ? `<button type="button" class="btn btn-outline-orange btn-sm js-quick-compare" data-from-id="${prevSnap.id}" data-to-id="${snap.id}">
               Compare v${prevSnap.version_number} &rarr; v${snap.version_number}
             </button>`
          : `<span class="badge badge-tag">Initial Baseline</span>`;

        const kwBadge = snap.matched_keywords
          ? `<span class="badge badge-changed">Keywords: ${escapeHtml(snap.matched_keywords)}</span>`
          : "";

        return `
          <div id="version-${snap.version_number}" class="notif-item">
            <div style="min-width: 0;">
              <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                <span class="badge badge-unchanged">Version #${snap.version_number}</span>
                <span class="badge badge-tag" title="SHA-256 Content Hash">SHA-256: ${escapeHtml(snap.content_hash)}</span>
                <span class="badge badge-tag">${snap.word_count} words (${snap.char_count} chars)</span>
                ${kwBadge}
              </div>
              <p class="notif-msg" style="margin-top: 0.45rem;">
                <strong>Summary:</strong> ${escapeHtml(snap.change_summary || "Snapshot captured.")}
              </p>
            </div>
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 0.45rem;">
              <span style="font-size: 0.78rem; color: var(--text-muted); white-space: nowrap;">
                ${escapeHtml(formatDateTime(snap.created_at))}
              </span>
              ${compareBtnHtml}
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderCheckRuns(checks) {
    if (!checks || checks.length === 0) {
      checkRunsList.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 1.25rem;">
          No check runs recorded yet.
        </div>
      `;
      return;
    }

    checkRunsList.innerHTML = checks
      .map((run) => {
        let badge = `<span class="badge badge-unchanged">${escapeHtml(run.status)}</span>`;
        if (run.status === "changed") {
          badge = `<span class="badge badge-changed">Changed</span>`;
        } else if (run.status === "failed") {
          badge = `<span class="badge badge-error">Failed</span>`;
        } else if (run.status === "initial") {
          badge = `<span class="badge badge-pending">Initial Baseline</span>`;
        }

        const httpInfo = run.http_status_code ? `HTTP ${run.http_status_code}` : "No HTTP response";
        return `
          <div class="notif-item">
            <div>
              <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                ${badge}
                <span class="badge badge-tag">${escapeHtml(httpInfo)}</span>
                <span class="badge badge-tag">${run.duration_ms} ms</span>
                ${
                  run.content_hash
                    ? `<span class="badge badge-tag">Hash: ${escapeHtml(run.content_hash.slice(0, 16))}...</span>`
                    : ""
                }
              </div>
              ${
                run.error_message
                  ? `<p style="color: #fca5a5; font-size: 0.82rem; margin-top: 0.35rem;"><strong>${escapeHtml(run.error_type || "Error")}:</strong> ${escapeHtml(run.error_message)}</p>`
                  : ""
              }
            </div>
            <div style="font-size: 0.78rem; color: var(--text-muted); white-space: nowrap;">
              ${escapeHtml(formatDateTime(run.checked_at))}
            </div>
          </div>
        `;
      })
      .join("");
  }

  async function loadHistoryPage() {
    try {
      const res = await fetch(`/api/sites/${siteId}/history`);
      if (!res.ok) {
        throw new Error("Could not load website history.");
      }
      const data = await res.json();
      const site = data.site;
      snapshots = data.snapshots || [];

      updateStatusBadge(site.status);
      metaInterval.textContent = `Every ${site.interval_minutes} minutes`;
      metaLastChecked.textContent = formatDateTime(site.last_checked_at);
      metaLastChanged.textContent = formatDateTime(site.last_changed_at);
      metaSnapshotCount.textContent = String(snapshots.length);

      const configBadges = [];
      if (site.css_selector) {
        configBadges.push(`<span class="badge badge-tag">Selector: ${escapeHtml(site.css_selector)}</span>`);
      }
      if (site.ignore_selector) {
        configBadges.push(`<span class="badge badge-tag">Ignore: ${escapeHtml(site.ignore_selector)}</span>`);
      }
      if (site.keywords) {
        configBadges.push(`<span class="badge badge-tag">Keywords: ${escapeHtml(site.keywords)}</span>`);
      }
      historyConfigTags.innerHTML = configBadges.join("");

      renderSnapshotsTimeline(snapshots);
      renderCheckRuns(data.recent_checks || []);

      if (snapshots.length === 0) {
        diffControlsBar.style.display = "none";
        diffStatsStrip.style.display = "none";
        sideBySideView.style.display = "none";
        unifiedDiffView.style.display = "none";
        beforeAfterView.style.display = "none";
        diffNoChangesBanner.style.display = "none";
        diffEmptyState.style.display = "block";
        return;
      }

      diffEmptyState.style.display = "none";
      diffControlsBar.style.display = "flex";

      // Populate selector options (snapshots are sorted newest first)
      const optionsHtml = snapshots
        .map(
          (s) =>
            `<option value="${s.id}">v${s.version_number} — ${escapeHtml(formatDateTime(s.created_at))} (${escapeHtml(s.content_hash.slice(0, 10))}...)</option>`
        )
        .join("");

      fromVersionSelect.innerHTML = optionsHtml;
      toVersionSelect.innerHTML = optionsHtml;

      const newestSnap = snapshots[0];
      const olderSnap = snapshots.length >= 2 ? snapshots[1] : snapshots[0];

      fromVersionSelect.value = String(olderSnap.id);
      toVersionSelect.value = String(newestSnap.id);

      await loadDiffForPair(olderSnap.id, newestSnap.id);
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // Event Listeners
  compareVersionsBtn.addEventListener("click", () => {
    const fromId = Number(fromVersionSelect.value);
    const toId = Number(toVersionSelect.value);
    loadDiffForPair(fromId, toId);
  });

  document.querySelectorAll(".filter-tab[data-diff-mode]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab[data-diff-mode]").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentDiffMode = tab.getAttribute("data-diff-mode");
      applyActiveDiffTab();
    });
  });

  snapshotsTimelineList.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-quick-compare");
    if (!btn) return;
    const fromId = Number(btn.getAttribute("data-from-id"));
    const toId = Number(btn.getAttribute("data-to-id"));
    fromVersionSelect.value = String(fromId);
    toVersionSelect.value = String(toId);
    loadDiffForPair(fromId, toId);
    window.scrollTo({ top: 180, behavior: "smooth" });
  });

  historyCheckNowBtn.addEventListener("click", async () => {
    const orig = historyCheckNowBtn.innerHTML;
    historyCheckNowBtn.disabled = true;
    historyCheckNowBtn.innerHTML = `<span class="spinner"></span> Checking...`;
    try {
      const res = await fetch(`/api/sites/${siteId}/check`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Check failed.");
      }
      showToast(data.message || "Check completed.", data.check_run?.status === "failed" ? "error" : "success");
      await loadHistoryPage();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      historyCheckNowBtn.disabled = false;
      historyCheckNowBtn.innerHTML = orig;
    }
  });

  loadHistoryPage();
})();
