/* ==========================================================================
   Website Change Detector — Dashboard Frontend Controller
   ========================================================================== */

(() => {
  "use strict";

  let allSites = [];
  let activeFilter = "all";
  let searchQuery = "";
  let pendingDeleteSiteId = null;

  // DOM References
  const statTotalSites = document.getElementById("statTotalSites");
  const statSnapshotsSub = document.getElementById("statSnapshotsSub");
  const statChangedSites = document.getElementById("statChangedSites");
  const statSuccessfulChecks = document.getElementById("statSuccessfulChecks");
  const statFailedChecks = document.getElementById("statFailedChecks");
  const statErrorSitesSub = document.getElementById("statErrorSitesSub");
  const activityChartContainer = document.getElementById("activityChartContainer");
  const healthText = document.getElementById("healthText");

  const sitesLoadingState = document.getElementById("sitesLoadingState");
  const sitesEmptyState = document.getElementById("sitesEmptyState");
  const sitesListContainer = document.getElementById("sitesListContainer");
  const siteSearchInput = document.getElementById("siteSearchInput");

  const notificationsContainer = document.getElementById("notificationsContainer");
  const notifTotalBadge = document.getElementById("notifTotalBadge");

  // Add Site Modal DOM
  const addSiteModal = document.getElementById("addSiteModal");
  const addSiteForm = document.getElementById("addSiteForm");
  const addSiteErrorBox = document.getElementById("addSiteErrorBox");
  const submitAddSiteBtn = document.getElementById("submitAddSiteBtn");
  const siteIntervalInput = document.getElementById("siteIntervalInput");
  const runInitialCheckCheckbox = document.getElementById("runInitialCheckCheckbox");

  // Delete Modal DOM
  const deleteConfirmModal = document.getElementById("deleteConfirmModal");
  const deleteSiteNameLabel = document.getElementById("deleteSiteNameLabel");
  const confirmDeleteBtn = document.getElementById("confirmDeleteBtn");

  // Toast Container
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
      const date = new Date(isoString);
      if (Number.isNaN(date.getTime())) return "Never";
      return date.toLocaleString(undefined, {
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

  function formatInterval(minutes) {
    const mins = Number(minutes) || 60;
    if (mins < 60) return `Every ${mins}m`;
    const hrs = (mins / 60).toFixed(mins % 60 === 0 ? 0 : 1);
    return `Every ${hrs}h (${mins}m)`;
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 4500);
  }

  function validatePublicUrlClientSide(urlStr) {
    if (!urlStr || !urlStr.trim()) {
      return "Please enter a website URL.";
    }
    let parsed;
    try {
      parsed = new URL(urlStr.trim());
    } catch {
      return "Invalid URL format. Include http:// or https:// (e.g., https://example.com).";
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "Only public http:// and https:// URLs are permitted.";
    }
    if (parsed.username || parsed.password) {
      return "URLs with embedded credentials are not permitted.";
    }
    const host = (parsed.hostname || "").toLowerCase();
    if (
      host === "localhost" ||
      host === "0.0.0.0" ||
      host === "::1" ||
      host === "[::1]" ||
      host.endsWith(".local") ||
      host.endsWith(".internal") ||
      host.endsWith(".localhost")
    ) {
      return `Target host '${host}' is internal or loopback and is blocked by SSRF policy.`;
    }
    // Check private IPv4 ranges
    const ipv4Match = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (ipv4Match) {
      const [a, b] = [Number(ipv4Match[1]), Number(ipv4Match[2])];
      if (
        a === 127 ||
        a === 10 ||
        a === 0 ||
        (a === 172 && b >= 16 && b <= 31) ||
        (a === 192 && b === 168) ||
        (a === 169 && b === 254) ||
        (a === 100 && b >= 64 && b <= 127)
      ) {
        return `IP address '${host}' is in a private or loopback range and cannot be monitored.`;
      }
    } else if (!host.includes(".")) {
      return `Hostname '${host}' is not a valid public domain.`;
    }
    return null;
  }

  function getStatusBadgeHtml(status) {
    const normalized = (status || "pending").toLowerCase();
    if (normalized === "changed") {
      return `<span class="badge badge-changed">Changed</span>`;
    }
    if (normalized === "unchanged") {
      return `<span class="badge badge-unchanged">Unchanged</span>`;
    }
    if (normalized === "error") {
      return `<span class="badge badge-error">Check Failed</span>`;
    }
    return `<span class="badge badge-pending">Pending Check</span>`;
  }

  function renderActivityChart(timeline) {
    if (!activityChartContainer) return;
    if (!Array.isArray(timeline) || timeline.length === 0) {
      activityChartContainer.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 1.5rem;">No check activity recorded yet.</div>`;
      return;
    }

    let maxVal = 1;
    timeline.forEach((pt) => {
      maxVal = Math.max(maxVal, pt.changes || 0, pt.successful_checks || 0, pt.failed_checks || 0);
    });

    activityChartContainer.innerHTML = timeline
      .map((pt) => {
        const chHeight = Math.max(4, Math.round(((pt.changes || 0) / maxVal) * 90));
        const okHeight = Math.max(4, Math.round(((pt.successful_checks || 0) / maxVal) * 90));
        const failHeight = Math.max(4, Math.round(((pt.failed_checks || 0) / maxVal) * 90));
        const dayShort = (pt.date || "").slice(5);
        return `
          <div class="chart-col" title="${escapeHtml(pt.date)} — Changes: ${pt.changes}, OK: ${pt.successful_checks}, Failed: ${pt.failed_checks}">
            <div class="chart-bar-group">
              <div class="chart-bar bar-changes" style="height: ${chHeight}px;"></div>
              <div class="chart-bar bar-ok" style="height: ${okHeight}px;"></div>
              <div class="chart-bar bar-fail" style="height: ${failHeight}px;"></div>
            </div>
            <span class="chart-day-label">${escapeHtml(dayShort)}</span>
          </div>
        `;
      })
      .join("");
  }

  function renderSitesList() {
    const filtered = allSites.filter((site) => {
      if (activeFilter !== "all" && site.status !== activeFilter) {
        return false;
      }
      if (searchQuery) {
        const haystack = `${site.name} ${site.url} ${site.keywords || ""} ${site.css_selector || ""}`.toLowerCase();
        if (!haystack.includes(searchQuery)) return false;
      }
      return true;
    });

    if (allSites.length === 0) {
      sitesEmptyState.style.display = "block";
      sitesListContainer.innerHTML = "";
      return;
    }

    sitesEmptyState.style.display = "none";

    if (filtered.length === 0) {
      sitesListContainer.innerHTML = `
        <div class="empty-state">
          <h3>No Matching Websites</h3>
          <p>Try clearing your search filter or switching status tabs.</p>
        </div>
      `;
      return;
    }

    sitesListContainer.innerHTML = filtered
      .map((site) => {
        const statusClass = `status-${escapeHtml(site.status || "pending")}`;
        const badgeHtml = getStatusBadgeHtml(site.status);

        const tags = [];
        if (site.css_selector) {
          tags.push(`<span class="badge badge-tag" title="Target CSS Selector">Selector: ${escapeHtml(site.css_selector)}</span>`);
        }
        if (site.ignore_selector) {
          tags.push(`<span class="badge badge-tag" title="Ignored CSS Selector">Ignore: ${escapeHtml(site.ignore_selector)}</span>`);
        }
        if (site.keywords) {
          tags.push(`<span class="badge badge-tag" title="Monitored Keywords">Keywords: ${escapeHtml(site.keywords)}</span>`);
        }
        if (site.keyword_only_alerts) {
          tags.push(`<span class="badge badge-tag">Keyword-Only Alerts</span>`);
        }

        const httpBadge = site.last_status_code
          ? `HTTP ${site.last_status_code} (${site.last_check_duration_ms || 0} ms)`
          : "Not checked yet";

        let bannerHtml = "";
        if (site.status === "error" && site.last_error) {
          bannerHtml = `<div class="site-error-banner"><strong>Last Error:</strong> ${escapeHtml(site.last_error)}</div>`;
        } else if (site.last_change_summary) {
          bannerHtml = `<div class="site-summary-banner"><strong>Latest Summary:</strong> ${escapeHtml(site.last_change_summary)}</div>`;
        }

        return `
          <article class="site-card ${statusClass}" data-site-id="${site.id}">
            <div class="site-card-top">
              <div class="site-title-group">
                <div class="site-name-row">
                  <span class="site-name">${escapeHtml(site.name)}</span>
                  ${badgeHtml}
                  ${tags.join("")}
                </div>
                <a href="${escapeHtml(site.url)}" target="_blank" rel="noopener noreferrer" class="site-url">
                  ${escapeHtml(site.url)}
                </a>
              </div>
              <div class="site-actions">
                <button
                  type="button"
                  class="btn btn-primary btn-sm js-check-now-btn"
                  data-site-id="${site.id}"
                >
                  Check Now
                </button>
                <a
                  href="/sites/${site.id}/history"
                  class="btn btn-secondary btn-sm"
                >
                  History &amp; Diff (${site.snapshot_count || 0})
                </a>
                <a
                  href="/api/sites/${site.id}/rss"
                  target="_blank"
                  rel="noopener"
                  class="btn btn-outline-orange btn-sm"
                  title="Page-specific RSS Feed"
                >
                  RSS
                </a>
                <button
                  type="button"
                  class="btn btn-danger btn-sm js-delete-site-btn"
                  data-site-id="${site.id}"
                  data-site-name="${escapeHtml(site.name)}"
                >
                  Delete
                </button>
              </div>
            </div>

            <div class="site-meta-grid">
              <div class="meta-item">
                <span class="meta-label">Check Interval</span>
                <span class="meta-value">${escapeHtml(formatInterval(site.interval_minutes))}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Last Checked</span>
                <span class="meta-value">${escapeHtml(formatDateTime(site.last_checked_at))}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Last Changed</span>
                <span class="meta-value">${escapeHtml(formatDateTime(site.last_changed_at))}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Last HTTP Result</span>
                <span class="meta-value">${escapeHtml(httpBadge)}</span>
              </div>
            </div>

            ${bannerHtml}
          </article>
        `;
      })
      .join("");
  }

  function renderNotifications(notifications) {
    notifTotalBadge.textContent = `${notifications.length} alert(s)`;
    if (!notifications || notifications.length === 0) {
      notificationsContainer.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 1.25rem;">
          No change alerts triggered yet. Initial baseline snapshots do not generate false alerts.
        </div>
      `;
      return;
    }

    notificationsContainer.innerHTML = notifications
      .map((notif) => {
        const statusBadge =
          notif.delivery_status === "delivered"
            ? `<span class="badge badge-unchanged">Delivered</span>`
            : `<span class="badge badge-error">${escapeHtml(notif.delivery_status)}</span>`;

        const channelBadge = `<span class="badge badge-tag">${escapeHtml(notif.channel)}</span>`;
        const triggerBadge =
          notif.trigger_type === "keyword_match"
            ? `<span class="badge badge-changed">Keyword Alert</span>`
            : `<span class="badge badge-pending">Content Change</span>`;

        return `
          <div class="notif-item">
            <div>
              <div style="display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap;">
                <span class="notif-title">${escapeHtml(notif.title)}</span>
                ${triggerBadge}
                ${channelBadge}
                ${statusBadge}
              </div>
              <p class="notif-msg">${escapeHtml(notif.message)}</p>
              ${
                notif.error_detail
                  ? `<p style="color: #fca5a5; font-size: 0.78rem; margin-top: 0.25rem;">Delivery Error: ${escapeHtml(notif.error_detail)}</p>`
                  : ""
              }
            </div>
            <div style="font-size: 0.78rem; color: var(--text-muted); white-space: nowrap;">
              ${escapeHtml(formatDateTime(notif.created_at))}
            </div>
          </div>
        `;
      })
      .join("");
  }

  async function loadDashboardData(showLoadingSpinner = false) {
    if (showLoadingSpinner && allSites.length === 0) {
      sitesLoadingState.style.display = "block";
    }
    try {
      const [statsRes, sitesRes, notifRes, healthRes] = await Promise.all([
        fetch("/api/stats"),
        fetch("/api/sites"),
        fetch("/api/notifications?limit=15"),
        fetch("/api/health"),
      ]);

      if (statsRes.ok) {
        const stats = await statsRes.json();
        statTotalSites.textContent = stats.total_sites;
        statSnapshotsSub.textContent = `${stats.total_snapshots} saved version snapshot(s)`;
        statChangedSites.textContent = stats.sites_with_changes;
        statSuccessfulChecks.textContent = stats.successful_checks;
        statFailedChecks.textContent = stats.failed_checks;
        statErrorSitesSub.textContent = `${stats.sites_in_error} website(s) currently in error state`;
        renderActivityChart(stats.timeline);
      }

      if (sitesRes.ok) {
        allSites = await sitesRes.json();
        renderSitesList();
      }

      if (notifRes.ok) {
        const notifs = await notifRes.json();
        renderNotifications(notifs);
      }

      if (healthRes.ok) {
        const health = await healthRes.json();
        healthText.textContent = health.scheduler_running
          ? "Scheduler Active"
          : "Service Online";
      }
    } catch (err) {
      showToast(`Failed to refresh dashboard data: ${err.message}`, "error");
    } finally {
      sitesLoadingState.style.display = "none";
    }
  }

  async function handleManualCheck(siteId, buttonEl) {
    const originalHtml = buttonEl.innerHTML;
    buttonEl.disabled = true;
    buttonEl.innerHTML = `<span class="spinner"></span> Checking...`;

    try {
      const res = await fetch(`/api/sites/${siteId}/check`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Check failed.");
      }
      const runStatus = data.check_run ? data.check_run.status : "";
      if (runStatus === "failed") {
        showToast(data.message || "Check failed.", "error");
      } else if (runStatus === "changed") {
        showToast(data.message || "Meaningful change detected!", "success");
      } else {
        showToast(data.message || "Check completed.", "success");
      }
      await loadDashboardData(false);
    } catch (err) {
      showToast(err.message || "Error running manual check.", "error");
      buttonEl.disabled = false;
      buttonEl.innerHTML = originalHtml;
    }
  }

  function openAddModal() {
    addSiteErrorBox.classList.remove("visible");
    addSiteErrorBox.textContent = "";
    addSiteModal.classList.add("open");
    document.getElementById("siteNameInput").focus();
  }

  function closeAddModal() {
    addSiteModal.classList.remove("open");
  }

  function openDeleteModal(siteId, siteName) {
    pendingDeleteSiteId = siteId;
    deleteSiteNameLabel.textContent = siteName;
    deleteConfirmModal.classList.add("open");
  }

  function closeDeleteModal() {
    pendingDeleteSiteId = null;
    deleteConfirmModal.classList.remove("open");
  }

  // Event Listeners
  document.getElementById("openAddModalBtn").addEventListener("click", openAddModal);
  document.getElementById("openAddModalBtnSecondary").addEventListener("click", openAddModal);
  document.getElementById("emptyStateAddBtn").addEventListener("click", openAddModal);
  document.getElementById("closeAddModalBtn").addEventListener("click", closeAddModal);
  document.getElementById("cancelAddModalBtn").addEventListener("click", closeAddModal);

  document.getElementById("closeDeleteModalBtn").addEventListener("click", closeDeleteModal);
  document.getElementById("cancelDeleteBtn").addEventListener("click", closeDeleteModal);

  document.getElementById("refreshDashboardBtn").addEventListener("click", () => {
    loadDashboardData(false);
    showToast("Dashboard refreshed.", "info");
  });

  // Interval Preset Chips
  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      siteIntervalInput.value = chip.getAttribute("data-interval");
    });
  });

  // Status Filter Tabs
  document.querySelectorAll(".filter-tab[data-filter]").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab[data-filter]").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      activeFilter = tab.getAttribute("data-filter");
      renderSitesList();
    });
  });

  // Search Input
  siteSearchInput.addEventListener("input", (e) => {
    searchQuery = (e.target.value || "").trim().toLowerCase();
    renderSitesList();
  });

  // Delegated clicks on site cards
  sitesListContainer.addEventListener("click", (e) => {
    const checkBtn = e.target.closest(".js-check-now-btn");
    if (checkBtn) {
      const siteId = Number(checkBtn.getAttribute("data-site-id"));
      handleManualCheck(siteId, checkBtn);
      return;
    }

    const deleteBtn = e.target.closest(".js-delete-site-btn");
    if (deleteBtn) {
      const siteId = Number(deleteBtn.getAttribute("data-site-id"));
      const siteName = deleteBtn.getAttribute("data-site-name") || `Site #${siteId}`;
      openDeleteModal(siteId, siteName);
    }
  });

  // Confirm Delete
  confirmDeleteBtn.addEventListener("click", async () => {
    if (!pendingDeleteSiteId) return;
    confirmDeleteBtn.disabled = true;
    try {
      const res = await fetch(`/api/sites/${pendingDeleteSiteId}`, { method: "DELETE" });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Failed to delete website.");
      }
      showToast("Website and its history deleted.", "success");
      closeDeleteModal();
      await loadDashboardData(false);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      confirmDeleteBtn.disabled = false;
    }
  });

  // Submit Add Website Form
  addSiteForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    addSiteErrorBox.classList.remove("visible");

    const name = document.getElementById("siteNameInput").value.trim();
    const url = document.getElementById("siteUrlInput").value.trim();
    const intervalMinutes = Number(siteIntervalInput.value);
    const keywords = document.getElementById("siteKeywordsInput").value.trim() || null;
    const cssSelector = document.getElementById("siteCssSelectorInput").value.trim() || null;
    const ignoreSelector = document.getElementById("siteIgnoreSelectorInput").value.trim() || null;
    const keywordOnlyAlerts = document.getElementById("siteKeywordOnlyCheckbox").checked;
    const usePlaywright = document.getElementById("sitePlaywrightCheckbox").checked;
    const shouldCheckImmediately = runInitialCheckCheckbox.checked;

    if (!name) {
      addSiteErrorBox.textContent = "Website name is required.";
      addSiteErrorBox.classList.add("visible");
      return;
    }

    const urlError = validatePublicUrlClientSide(url);
    if (urlError) {
      addSiteErrorBox.textContent = urlError;
      addSiteErrorBox.classList.add("visible");
      return;
    }

    if (!Number.isInteger(intervalMinutes) || intervalMinutes < 15 || intervalMinutes > 1440) {
      addSiteErrorBox.textContent =
        "Check interval must be an integer between 15 minutes and 1440 minutes (24 hours).";
      addSiteErrorBox.classList.add("visible");
      return;
    }

    submitAddSiteBtn.disabled = true;
    submitAddSiteBtn.innerHTML = `<span class="spinner"></span> Saving...`;

    try {
      const res = await fetch("/api/sites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          url,
          interval_minutes: intervalMinutes,
          keywords,
          keyword_only_alerts: keywordOnlyAlerts,
          css_selector: cssSelector,
          ignore_selector: ignoreSelector,
          use_playwright: usePlaywright,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        let errMsg = "Failed to add website.";
        if (typeof data.detail === "string") {
          errMsg = data.detail;
        } else if (Array.isArray(data.detail) && data.detail.length > 0) {
          errMsg = data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
        }
        throw new Error(errMsg);
      }

      addSiteForm.reset();
      siteIntervalInput.value = "60";
      runInitialCheckCheckbox.checked = true;
      closeAddModal();
      showToast(`Added "${data.name}" to monitored websites.`, "success");

      if (shouldCheckImmediately && data.id) {
        showToast(`Capturing initial baseline snapshot for "${data.name}"...`, "info");
        await fetch(`/api/sites/${data.id}/check`, { method: "POST" }).catch(() => {});
      }

      await loadDashboardData(false);
    } catch (err) {
      addSiteErrorBox.textContent = err.message;
      addSiteErrorBox.classList.add("visible");
    } finally {
      submitAddSiteBtn.disabled = false;
      submitAddSiteBtn.textContent = "Add Website";
    }
  });

  // Initial load & periodic UI refresh every 30s
  loadDashboardData(true);
  setInterval(() => loadDashboardData(false), 30000);
})();
