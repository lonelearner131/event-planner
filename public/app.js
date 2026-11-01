/**
 * Celebration & Event Planner — app.js
 *
 * IIFE with strict mode. ES2020 compatible (no build step).
 * No globals leak. No innerHTML with dynamic data.
 * JSDoc on all major functions.
 *
 * Security: all dynamic content is inserted via textContent, createElement,
 * setAttribute, or appendChild — never innerHTML with user/API data.
 */
(function () {
  "use strict";

  // =========================================================================
  // State
  // =========================================================================

  /** @type {object|null} Last successful plan response */
  let currentPlan = null;
  /** @type {object|null} Last successful form request object */
  let currentRequest = null;
  /** @type {Map<string, string>} Vendor ID → status */
  const vendorStatuses = new Map();

  // =========================================================================
  // DOM references
  // =========================================================================

  const form = document.getElementById("event-form");
  const submitBtn = document.getElementById("submit-btn");
  const resetBtn = document.getElementById("reset-btn");
  const loadingOverlay = document.getElementById("loading-overlay");
  const resultsSection = document.getElementById("results-section");
  const resultsHeading = document.getElementById("results-heading");
  const errorSummary = document.getElementById("error-summary");
  const errorList = document.getElementById("error-list");
  const communitySection = document.getElementById("community-section");
  const accessibilityDetailsGroup = document.getElementById("accessibility_details-group");
  const accessibilityCheckbox = document.getElementById("accessibility_needs");
  const eventTypeSelect = document.getElementById("event_type");
  const whatifBtn = document.getElementById("what-if-btn") || document.getElementById("whatif-btn");
  const exportIcsBtn = document.getElementById("export-ics-btn");
  const exportCsvBtn = document.getElementById("export-csv-btn");

  // =========================================================================
  // Utility: safe DOM helpers
  // =========================================================================

  /**
   * Create an element with optional attributes and text content.
   * @param {string} tag
   * @param {object} [attrs]
   * @param {string} [text]
   * @returns {HTMLElement}
   */
  function el(tag, attrs, text) {
    const elem = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") {
          elem.className = v;
        } else {
          elem.setAttribute(k, v);
        }
      }
    }
    if (text !== undefined) elem.textContent = text;
    return elem;
  }

  /**
   * Remove all children from a DOM node.
   * @param {HTMLElement} node
   */
  function clearEl(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /**
   * Set or remove the hidden attribute on an element.
   * @param {HTMLElement} node
   * @param {boolean} hidden
   */
  function setHidden(node, hidden) {
    if (hidden) {
      node.setAttribute("hidden", "");
      node.setAttribute("aria-hidden", "true");
    } else {
      node.removeAttribute("hidden");
      node.removeAttribute("aria-hidden");
    }
  }

  // =========================================================================
  // Currency formatting (Intl)
  // =========================================================================

  /**
   * Format an amount using Intl.NumberFormat.
   * @param {number} amount
   * @param {string} currency - ISO 4217 code
   * @returns {string}
   */
  function formatCurrency(amount, currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency,
        maximumFractionDigits: 0,
      }).format(amount);
    } catch {
      return `${currency} ${amount.toLocaleString()}`;
    }
  }

  /**
   * Format a date as a locale-friendly string.
   * @param {string} isoDate - YYYY-MM-DD
   * @returns {string}
   */
  function formatDate(isoDate) {
    try {
      const d = new Date(isoDate + "T00:00:00");
      return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch {
      return isoDate;
    }
  }

  /**
   * Count calendar days from today to a date string.
   * @param {string} isoDate
   * @returns {number}
   */
  function daysUntil(isoDate) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const target = new Date(isoDate + "T00:00:00");
    return Math.round((target - today) / 86400000);
  }

  // =========================================================================
  // Progressive disclosure
  // =========================================================================

  function onEventTypeChange() {
    const val = eventTypeSelect.value;
    const isCommunity = val === "community";
    setHidden(communitySection, !isCommunity);
    communitySection.disabled = !isCommunity;
  }

  function onAccessibilityChange() {
    const checked = accessibilityCheckbox.checked;
    setHidden(accessibilityDetailsGroup, !checked);
    accessibilityCheckbox.setAttribute("aria-checked", String(checked));
  }

  eventTypeSelect.addEventListener("change", onEventTypeChange);
  accessibilityCheckbox.addEventListener("change", onAccessibilityChange);

  // =========================================================================
  // Preset loader
  // =========================================================================

  const PRESETS = {
    wedding: {
      event_type: "wedding",
      event_date: offsetDate(240),
      guest_count: "80",
      total_budget: "25000",
      currency: "USD",
      location_type: "indoor",
      theme_or_vibe: "elegant garden romance",
      priorities: ["food", "photography", "decor"],
      diy_preference: "medium",
      has_children: false,
      accessibility_needs: false,
      dietary_needs: ["vegetarian"],
    },
    birthday: {
      event_type: "birthday",
      event_date: offsetDate(42),
      guest_count: "30",
      total_budget: "3000",
      currency: "USD",
      location_type: "indoor",
      theme_or_vibe: "tropical fiesta",
      priorities: ["food", "entertainment"],
      diy_preference: "medium",
      has_children: true,
      accessibility_needs: false,
      dietary_needs: ["vegan", "gluten_free"],
    },
    community: {
      event_type: "community",
      event_date: offsetDate(90),
      guest_count: "200",
      total_budget: "12000",
      currency: "GBP",
      location_type: "outdoor",
      theme_or_vibe: "summer community fair",
      priorities: ["guest_experience", "food", "entertainment"],
      diy_preference: "high",
      has_children: true,
      accessibility_needs: true,
      dietary_needs: ["halal", "vegan"],
      is_public: true,
      ticketed: false,
      expected_volunteers: 15,
    },
  };

  /**
   * Returns an ISO date string N days from today.
   * @param {number} days
   * @returns {string}
   */
  function offsetDate(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }

  /**
   * Populate the form with preset values.
   * @param {string} presetKey
   */
  function loadPreset(presetKey) {
    const preset = PRESETS[presetKey];
    if (!preset) return;

    // Reset first
    form.reset();
    clearErrors();

    // Core fields
    for (const [key, val] of Object.entries(preset)) {
      if (key === "priorities" || key === "dietary_needs") continue;
      const field = form.elements[key];
      if (!field) continue;
      if (field.type === "checkbox") {
        field.checked = Boolean(val);
        field.setAttribute("aria-checked", String(Boolean(val)));
      } else {
        field.value = val;
      }
    }

    // Checkboxes: priorities
    form.querySelectorAll("input[name='priorities']").forEach((cb) => {
      cb.checked = (preset.priorities || []).includes(cb.value);
    });

    // Checkboxes: dietary_needs
    form.querySelectorAll("input[name='dietary_needs']").forEach((cb) => {
      cb.checked = (preset.dietary_needs || []).includes(cb.value);
    });

    onEventTypeChange();
    onAccessibilityChange();

    // Scroll to form
    document.getElementById("plan-form").scrollIntoView({ behavior: "smooth" });
  }

  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => loadPreset(btn.dataset.preset));
  });

  // =========================================================================
  // Form collection
  // =========================================================================

  /**
   * Collect form values into a request object.
   * @returns {object}
   */
  function collectFormData() {
    const fd = new FormData(form);

    const priorities = [];
    form.querySelectorAll("input[name='priorities']:checked").forEach((cb) => {
      priorities.push(cb.value);
    });

    const dietaryNeeds = [];
    form.querySelectorAll("input[name='dietary_needs']:checked").forEach((cb) => {
      dietaryNeeds.push(cb.value);
    });

    const data = {
      event_type: fd.get("event_type") || "",
      event_date: fd.get("event_date") || "",
      guest_count: parseInt(fd.get("guest_count") || "0", 10),
      total_budget: parseInt(fd.get("total_budget") || "0", 10),
      currency: fd.get("currency") || "USD",
      location_type: fd.get("location_type") || "indoor",
      diy_preference: fd.get("diy_preference") || "medium",
      has_children: form.elements["has_children"].checked,
      accessibility_needs: form.elements["accessibility_needs"].checked,
      priorities,
      dietary_needs: dietaryNeeds,
    };

    // Optional text fields
    const optionalText = [
      "city_region", "theme_or_vibe", "dietary_notes",
      "cultural_or_religious_notes", "accessibility_details",
    ];
    for (const key of optionalText) {
      const val = (fd.get(key) || "").trim();
      if (val) data[key] = val;
    }

    // Community fields
    if (data.event_type === "community") {
      data.is_public = form.elements["is_public"].checked;
      data.ticketed = form.elements["ticketed"].checked;
      const volVal = fd.get("expected_volunteers");
      if (volVal) data.expected_volunteers = parseInt(volVal, 10);
    }

    return data;
  }

  // =========================================================================
  // Client-side validation
  // =========================================================================

  const VALIDATORS = [
    {
      field: "event_type",
      check: (v) => v.event_type !== "",
      message: "Please select an event type.",
    },
    {
      field: "event_date",
      check: (v) => {
        if (!v.event_date) return false;
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const target = new Date(v.event_date + "T00:00:00");
        return target > today;
      },
      message: "Event date must be in the future.",
    },
    {
      field: "guest_count",
      check: (v) => v.guest_count >= 1 && v.guest_count <= 5000,
      message: "Guest count must be between 1 and 5,000.",
    },
    {
      field: "total_budget",
      check: (v) => v.total_budget >= 1,
      message: "Budget must be a positive number.",
    },
  ];

  /**
   * Validate form data client-side. Returns array of error objects.
   * @param {object} data
   * @returns {{field: string, message: string}[]}
   */
  function validate(data) {
    return VALIDATORS.filter((v) => !v.check(data)).map((v) => ({
      field: v.field,
      message: v.message,
    }));
  }

  function clearErrors() {
    setHidden(errorSummary, true);
    clearEl(errorList);
    form.querySelectorAll("[aria-invalid]").forEach((el) => {
      el.removeAttribute("aria-invalid");
    });
    form.querySelectorAll(".field-error").forEach((el) => {
      el.textContent = "";
    });
  }

  /**
   * Display validation errors in the summary and inline.
   * @param {{field: string, message: string}[]} errors
   */
  function showErrors(errors) {
    clearEl(errorList);
    for (const { field, message } of errors) {
      const li = el("li");
      const a = el("a", { href: `#${field}` }, message);
      li.appendChild(a);
      errorList.appendChild(li);

      // Inline error
      const fieldEl = document.getElementById(field);
      const errEl = document.getElementById(`${field}-error`);
      if (fieldEl) {
        fieldEl.setAttribute("aria-invalid", "true");
        fieldEl.setAttribute("aria-describedby",
          (fieldEl.getAttribute("aria-describedby") || "") + ` ${field}-error`
        );
      }
      if (errEl) errEl.textContent = message;
    }
    setHidden(errorSummary, false);
    errorSummary.focus();
  }

  // =========================================================================
  // API calls
  // =========================================================================

  /**
   * POST to an API endpoint and return parsed JSON.
   * @param {string} path
   * @param {object} body
   * @returns {Promise<object>}
   */
  async function apiPost(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      const detail = errData.detail || `HTTP ${response.status}`;
      throw Object.assign(new Error(detail), { status: response.status, data: errData });
    }
    return response.json();
  }

  // =========================================================================
  // Plan form submission
  // =========================================================================

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearErrors();

    const data = collectFormData();
    const errors = validate(data);

    if (errors.length > 0) {
      showErrors(errors);
      return;
    }

    // Show loading state
    loadingOverlay.removeAttribute("hidden");
    loadingOverlay.setAttribute("aria-busy", "true");
    submitBtn.disabled = true;

    try {
      const plan = await apiPost("/api/plan", data);
      currentPlan = plan;
      currentRequest = data;

      // Pre-populate what-if fields
      document.getElementById("whatif-budget").value = data.total_budget;
      document.getElementById("whatif-guests").value = data.guest_count;

      renderPlan(plan, data);
      setHidden(resultsSection, false);

      // Move focus to results heading
      resultsHeading.focus();

      // Show results nav link
      const navResults = document.getElementById("nav-results");
      if (navResults) navResults.removeAttribute("aria-hidden");

      // Scroll to results
      resultsSection.scrollIntoView({ behavior: "smooth" });

      // Async: fetch ideas (don't block plan render)
      fetchAndRenderIdeas(data, plan);

    } catch (err) {
      if (err.status === 422 && err.data && err.data.errors) {
        const apiErrors = err.data.errors.map((e) => ({
          field: e.field.split(" → ").pop() || "form",
          message: e.message,
        }));
        showErrors(apiErrors);
      } else {
        showErrors([{ field: "event_type", message: err.message || "An error occurred. Please try again." }]);
      }
    } finally {
      loadingOverlay.setAttribute("hidden", "");
      loadingOverlay.setAttribute("aria-busy", "false");
      submitBtn.disabled = false;
    }
  });

  // =========================================================================
  // What-if recompute
  // =========================================================================

  if (whatifBtn) {
    whatifBtn.addEventListener("click", async () => {
      if (!currentRequest) return;
      const newBudget = parseInt(document.getElementById("whatif-budget").value, 10);
      const newGuests = parseInt(document.getElementById("whatif-guests").value, 10);

      if (!newBudget || !newGuests || newBudget < 1 || newGuests < 1) return;

      const newReq = { ...currentRequest, total_budget: newBudget, guest_count: newGuests };

      whatifBtn.disabled = true;
      whatifBtn.textContent = "Computing…";

      try {
        const plan = await apiPost("/api/plan", newReq);
        currentPlan = plan;
        currentRequest = newReq;
        renderPlan(plan, newReq);
        fetchAndRenderIdeas(newReq, plan);
      } catch (err) {
        // Non-blocking — show in console only
        console.warn("What-if recompute failed:", err.message);
      } finally {
        whatifBtn.disabled = false;
        whatifBtn.textContent = "⚡ Recompute Plan";
      }
    });
  }

  // =========================================================================
  // Ideas fetch
  // =========================================================================

  /**
   * Fetch personalised ideas and render them into the ideas section.
   * @param {object} request
   * @param {object} plan
   */
  async function fetchAndRenderIdeas(request, plan) {
    const ideasLoading = document.getElementById("ideas-loading");
    const ideasContent = document.getElementById("ideas-content");
    const aiNotice = document.getElementById("ai-notice");
    const aiNoticeText = document.getElementById("ai-notice-text");

    ideasLoading.removeAttribute("hidden");
    ideasLoading.setAttribute("aria-busy", "true");
    setHidden(ideasContent, true);

    try {
      const result = await apiPost("/api/ideas", request);
      renderIdeas(result.ideas);
      setHidden(ideasContent, false);

      if (result.ai_notice) {
        aiNoticeText.textContent = result.ai_notice;
        setHidden(aiNotice, false);
      } else {
        setHidden(aiNotice, true);
      }
    } catch (err) {
      // Show a soft error in the ideas section
      clearEl(ideasContent);
      const notice = el("p", { className: "feasibility-explanation" },
        "Ideas could not be loaded. Please try again later."
      );
      ideasContent.appendChild(notice);
      setHidden(ideasContent, false);
    } finally {
      ideasLoading.setAttribute("hidden", "");
      ideasLoading.setAttribute("aria-busy", "false");
    }
  }

  // =========================================================================
  // Exports
  // =========================================================================

  /**
   * Request and trigger download of a binary file from the API.
   * @param {string} path
   * @param {object} body
   * @param {string} filename
   * @param {string} mimeType
   */
  async function downloadExport(path, body, filename, mimeType) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": mimeType },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Export failed: HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  if (exportIcsBtn) {
    exportIcsBtn.addEventListener("click", async () => {
      if (!currentPlan || !currentRequest) return;
      exportIcsBtn.disabled = true;
      try {
        await downloadExport(
          "/api/export/ics",
          { plan: currentPlan, request: currentRequest },
          "event-timeline.ics",
          "text/calendar"
        );
      } catch (err) {
        console.warn("ICS export failed:", err.message);
      } finally {
        exportIcsBtn.disabled = false;
      }
    });
  }

  if (exportCsvBtn) {
    exportCsvBtn.addEventListener("click", async () => {
      if (!currentPlan || !currentRequest) return;
      exportCsvBtn.disabled = true;
      try {
        await downloadExport(
          "/api/export/csv",
          { plan: currentPlan, request: currentRequest },
          "event-plan.csv",
          "text/csv"
        );
      } catch (err) {
        console.warn("CSV export failed:", err.message);
      } finally {
        exportCsvBtn.disabled = false;
      }
    });
  }

  resetBtn.addEventListener("click", () => {
    form.reset();
    clearErrors();
    onEventTypeChange();
    onAccessibilityChange();
  });

  // =========================================================================
  // Plan renderer
  // =========================================================================

  /**
   * Render the full plan into the results section.
   * @param {object} plan
   * @param {object} request
   */
  function renderPlan(plan, request) {
    renderSummaryCards(plan, request);
    renderWarnings(plan.warnings);
    renderBudget(plan.budget, request.currency);
    renderTimeline(plan.timeline);
    renderVendors(plan.vendors, plan.budget, request.currency);
  }

  // ---- Summary cards ----

  function renderSummaryCards(plan, request) {
    const currency = request.currency;
    document.getElementById("card-total-value").textContent =
      formatCurrency(plan.budget.total, currency);
    document.getElementById("card-per-guest-value").textContent =
      formatCurrency(plan.budget.feasibility.per_guest_cost, currency);

    const feasEl = document.getElementById("card-feasibility-value");
    feasEl.textContent = plan.budget.feasibility.status.charAt(0).toUpperCase() +
      plan.budget.feasibility.status.slice(1);
    feasEl.style.color = `var(--clr-${plan.budget.feasibility.status})`;

    document.getElementById("card-days-value").textContent =
      daysUntil(request.event_date).toLocaleString();
  }

  // ---- Warnings ----

  function renderWarnings(warnings) {
    const panel = document.getElementById("warnings-panel");
    const list = document.getElementById("warnings-list");
    if (!warnings || !warnings.items || warnings.items.length === 0) {
      setHidden(panel, true);
      return;
    }
    clearEl(list);
    for (const w of warnings.items) {
      list.appendChild(el("li", null, w));
    }
    setHidden(panel, false);
  }

  // ---- Budget ----

  /**
   * Render the budget table and feasibility analysis.
   * @param {object} budget
   * @param {string} currency
   */
  function renderBudget(budget, currency) {
    // Feasibility badge
    const badge = document.getElementById("feasibility-badge");
    const status = budget.feasibility.status;
    badge.className = `feasibility-badge feasibility-badge--${status}`;
    badge.textContent = `Budget rating: ${status.charAt(0).toUpperCase() + status.slice(1)}`;

    document.getElementById("feasibility-explanation").textContent =
      budget.feasibility.explanation;

    // Table body
    const tbody = document.getElementById("budget-tbody");
    clearEl(tbody);

    for (const item of budget.line_items) {
      const tr = el("tr");
      tr.appendChild(el("td", null, item.category));
      tr.appendChild(el("td", null, formatCurrency(item.amount, currency)));
      tr.appendChild(el("td", null, `${item.percent}%`));

      // Bar visual — text equivalent is in the Percent column
      const barTd = el("td");
      const barWrap = el("div", { className: "budget-bar-wrap" });
      const barFill = el("div", {
        className: item.category.toLowerCase().includes("contingency")
          ? "budget-bar-fill budget-bar-fill--contingency"
          : "budget-bar-fill",
      });
      barFill.style.width = `${item.percent}%`;
      barWrap.appendChild(barFill);
      barTd.appendChild(barWrap);
      tr.appendChild(barTd);

      tr.appendChild(el("td", null, item.saving_tip));
      tbody.appendChild(tr);
    }

    document.getElementById("budget-total-cell").textContent =
      `${formatCurrency(budget.total, currency)} (100%)`;

    // Suggestions
    const suggestionsPanel = document.getElementById("suggestions-panel");
    const suggestionsList = document.getElementById("suggestions-list");
    if (budget.feasibility.suggestions && budget.feasibility.suggestions.length > 0) {
      clearEl(suggestionsList);
      for (const s of budget.feasibility.suggestions) {
        suggestionsList.appendChild(el("li", null, s));
      }
      setHidden(suggestionsPanel, false);
    } else {
      setHidden(suggestionsPanel, true);
    }
  }

  // ---- Timeline ----

  /**
   * Render the timeline grouped by phase.
   * @param {object} timeline
   */
  function renderTimeline(timeline) {
    const meta = document.getElementById("timeline-meta");
    const compressed = timeline.compressed
      ? " The timeline is compressed — act on URGENT items immediately."
      : "";
    meta.textContent = `${timeline.lead_days} days until your event.${compressed}`;

    const container = document.getElementById("timeline-phases");
    clearEl(container);

    // Group by phase
    const phaseMap = new Map();
    for (const task of timeline.tasks) {
      if (!phaseMap.has(task.phase)) phaseMap.set(task.phase, []);
      phaseMap.get(task.phase).push(task);
    }

    for (const [phase, tasks] of phaseMap) {
      const phaseDiv = el("div", { className: "phase-group" });
      phaseDiv.appendChild(el("div", { className: "phase-title" }, phase));

      for (const task of tasks) {
        phaseDiv.appendChild(buildTaskCard(task));
      }
      container.appendChild(phaseDiv);
    }
  }

  /**
   * Build a task card element.
   * @param {object} task
   * @returns {HTMLElement}
   */
  function buildTaskCard(task) {
    const classes = ["task-card", `task-card--${task.priority}`];
    if (task.urgent) classes.push("task-card--urgent");

    const card = el("div", { className: classes.join(" ") });

    const dateDiv = el("div", { className: "task-date" }, formatDate(task.due_date));
    card.appendChild(dateDiv);

    const body = el("div", { className: "task-body" });
    body.appendChild(el("div", { className: "task-title" }, task.title));
    body.appendChild(el("div", { className: "task-desc" }, task.description));

    const badges = el("div", { className: "task-badges" });
    badges.appendChild(el("span", { className: `badge badge--${task.priority}` }, task.priority));
    if (task.urgent) {
      badges.appendChild(el("span", { className: "badge badge--urgent" }, "⚡ URGENT"));
    }
    body.appendChild(badges);
    card.appendChild(body);

    return card;
  }

  // ---- Vendors ----

  /**
   * Render the vendor checklist.
   * @param {object} vendors
   * @param {object} budget
   * @param {string} currency
   */
  function renderVendors(vendors, budget, currency) {
    const list = document.getElementById("vendor-list");
    clearEl(list);

    const items = vendors.items || [];
    document.getElementById("progress-total").textContent = items.length;

    for (const vendor of items) {
      if (!vendorStatuses.has(vendor.id)) {
        vendorStatuses.set(vendor.id, loadVendorStatus(vendor.id) || "todo");
      }
      list.appendChild(buildVendorCard(vendor, currency));
    }
    updateProgress(items.length);
  }

  /**
   * Build a collapsible vendor card.
   * @param {object} vendor
   * @param {string} currency
   * @returns {HTMLElement}
   */
  function buildVendorCard(vendor, currency) {
    const card = el("div", {
      className: "vendor-card",
      "aria-expanded": "false",
      id: `vendor-card-${vendor.id}`,
    });

    const status = vendorStatuses.get(vendor.id) || "todo";

    // Header (toggle button)
    const header = el("button", {
      type: "button",
      className: "vendor-card-header",
      "aria-controls": `vendor-body-${vendor.id}`,
      "aria-expanded": "false",
    });

    const titleWrap = el("div");
    titleWrap.appendChild(el("div", { className: "vendor-card-title" }, vendor.category));
    const bookBy = vendor.book_by_date
      ? ` · Book by ${formatDate(vendor.book_by_date)}`
      : "";
    const amountStr = vendor.linked_budget_amount > 0
      ? ` · ${formatCurrency(vendor.linked_budget_amount, currency)}`
      : "";
    titleWrap.appendChild(el("div", { className: "vendor-card-meta" }, `${status}${amountStr}${bookBy}`));
    header.appendChild(titleWrap);
    header.appendChild(el("span", { className: "vendor-card-chevron", "aria-hidden": "true" }, "▼"));

    header.addEventListener("click", () => {
      const expanded = card.getAttribute("aria-expanded") === "true";
      card.setAttribute("aria-expanded", String(!expanded));
      header.setAttribute("aria-expanded", String(!expanded));
    });
    card.appendChild(header);

    // Body
    const body = el("div", {
      className: "vendor-card-body",
      id: `vendor-body-${vendor.id}`,
      role: "region",
      "aria-labelledby": `vendor-card-${vendor.id}`,
    });

    // Why needed
    body.appendChild(el("p", { className: "feasibility-explanation" }, vendor.why_needed));

    // Status buttons
    const statusBar = el("div", {
      className: "vendor-status-bar",
      role: "group",
      "aria-label": `Status for ${vendor.category}`,
    });
    for (const s of ["todo", "contacted", "booked"]) {
      const btn = el("button", {
        type: "button",
        className: `vendor-status-btn${status === s ? ` active--${s}` : ""}`,
        "data-vendor-id": vendor.id,
        "data-status": s,
        "aria-pressed": String(status === s),
      }, s.charAt(0).toUpperCase() + s.slice(1));
      btn.addEventListener("click", (e) => {
        const vid = e.currentTarget.dataset.vendorId;
        const newStatus = e.currentTarget.dataset.status;
        setVendorStatus(vid, newStatus, card);
        updateProgress(document.querySelectorAll(".vendor-card").length);
      });
      statusBar.appendChild(btn);
    }
    body.appendChild(statusBar);

    // Questions, red flags, contract checklist
    appendVendorList(body, "Questions to ask", vendor.questions_to_ask);
    appendVendorList(body, "Red flags to watch for", vendor.red_flags);
    appendVendorList(body, "Contract checklist", vendor.contract_checklist);

    card.appendChild(body);
    return card;
  }

  /**
   * Append a titled list to a container.
   * @param {HTMLElement} container
   * @param {string} title
   * @param {string[]} items
   */
  function appendVendorList(container, title, items) {
    if (!items || items.length === 0) return;
    container.appendChild(el("div", { className: "vendor-section-title" }, title));
    const ul = el("ul", { className: "vendor-list-items" });
    for (const item of items) {
      ul.appendChild(el("li", null, item));
    }
    container.appendChild(ul);
  }

  /**
   * Update vendor status in state, localStorage, and DOM.
   * @param {string} vendorId
   * @param {string} newStatus
   * @param {HTMLElement} card
   */
  function setVendorStatus(vendorId, newStatus, card) {
    vendorStatuses.set(vendorId, newStatus);
    saveVendorStatus(vendorId, newStatus);

    // Update button states
    card.querySelectorAll(".vendor-status-btn").forEach((btn) => {
      const isActive = btn.dataset.status === newStatus;
      btn.className = `vendor-status-btn${isActive ? ` active--${newStatus}` : ""}`;
      btn.setAttribute("aria-pressed", String(isActive));
    });
  }

  /**
   * Update the progress bar.
   * @param {number} total
   */
  function updateProgress(total) {
    let booked = 0;
    vendorStatuses.forEach((v) => { if (v === "booked") booked++; });
    document.getElementById("progress-booked").textContent = booked;
    const pct = total > 0 ? Math.round((booked / total) * 100) : 0;
    document.getElementById("checklist-progress").value = pct;
  }

  // ---- localStorage for vendor status ----

  function saveVendorStatus(vendorId, status) {
    try {
      const key = "vendor_statuses";
      const existing = JSON.parse(localStorage.getItem(key) || "{}");
      existing[vendorId] = status;
      localStorage.setItem(key, JSON.stringify(existing));
    } catch {
      // Silently ignore storage errors
    }
  }

  function loadVendorStatus(vendorId) {
    try {
      const existing = JSON.parse(localStorage.getItem("vendor_statuses") || "{}");
      return existing[vendorId] || null;
    } catch {
      return null;
    }
  }

  // =========================================================================
  // Ideas renderer
  // =========================================================================

  /**
   * Render AI or curated ideas.
   * @param {object} ideas
   */
  function renderIdeas(ideas) {
    const container = document.getElementById("ideas-content");
    clearEl(container);

    // Themes
    if (ideas.themes && ideas.themes.length > 0) {
      const heading = el("h4", { style: "margin-bottom: var(--sp-3);" }, "✨ Theme Suggestions");
      container.appendChild(heading);
      const grid = el("div", { className: "themes-grid" });
      for (const theme of ideas.themes) {
        grid.appendChild(buildThemeCard(theme));
      }
      container.appendChild(grid);
    }

    // Ideas grid
    const grid = el("div", { className: "ideas-grid" });

    appendIdeasBlock(grid, "🎭 Activities", ideas.activities);
    appendIdeasBlock(grid, "🍽️ Menu Suggestions", ideas.menu_suggestions);
    appendIdeasBlock(grid, "✨ Décor Ideas", ideas.decor_ideas);
    appendIdeasBlock(grid, "🎵 Music Vibes", ideas.music_vibes);
    appendIdeasBlock(grid, "💰 Money-saving Tips", ideas.money_saving_ideas);
    appendIdeasBlock(grid, "♿ Accessibility Touches", ideas.accessibility_touches);

    container.appendChild(grid);

    // Sample invitation
    if (ideas.sample_invitation) {
      const invHeading = el("h4", { style: "margin-top: var(--sp-6); margin-bottom: var(--sp-3);" },
        "✉️ Sample Invitation"
      );
      container.appendChild(invHeading);
      const box = el("div", { className: "invitation-box" }, ideas.sample_invitation);
      container.appendChild(box);
    }
  }

  /**
   * Build a theme card element.
   * @param {object} theme
   * @returns {HTMLElement}
   */
  function buildThemeCard(theme) {
    const card = el("div", { className: "theme-card" });
    card.appendChild(el("div", { className: "theme-card-name" }, theme.name));
    card.appendChild(el("div", { className: "theme-card-desc" }, theme.description));

    if (theme.palette && theme.palette.length > 0) {
      const palette = el("div", { className: "theme-palette" });
      for (const color of theme.palette) {
        palette.appendChild(el("span", { className: "palette-chip" }, color));
      }
      card.appendChild(palette);
    }
    return card;
  }

  /**
   * Append an ideas block (heading + list) to a container.
   * @param {HTMLElement} container
   * @param {string} title
   * @param {string[]} items
   */
  function appendIdeasBlock(container, title, items) {
    if (!items || items.length === 0) return;
    const block = el("div", { className: "ideas-block" });
    block.appendChild(el("h4", null, title));
    const ul = el("ul");
    for (const item of items) {
      ul.appendChild(el("li", null, item));
    }
    block.appendChild(ul);
    container.appendChild(block);
  }

  // =========================================================================
  // Initialisation
  // =========================================================================

  // Set minimum date on the event date input to tomorrow
  (function setMinDate() {
    const input = document.getElementById("event_date");
    if (!input) return;
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    input.min = tomorrow.toISOString().slice(0, 10);

    // Set max to 3 years from now
    const maxDate = new Date();
    maxDate.setFullYear(maxDate.getFullYear() + 3);
    input.max = maxDate.toISOString().slice(0, 10);
  })();

  // Set initial state for community section
  onEventTypeChange();
  onAccessibilityChange();

  // Toggle aria-checked on all checkboxes with role=switch
  document.querySelectorAll('input[role="switch"]').forEach((cb) => {
    cb.addEventListener("change", (e) => {
      e.currentTarget.setAttribute("aria-checked", String(e.currentTarget.checked));
    });
  });

  // Debounce helper
  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  // Debounce what-if inputs so recompute is not triggered on every keystroke
  ["whatif-budget", "whatif-guests"].forEach((id) => {
    const input = document.getElementById(id);
    if (!input) return;
    input.addEventListener(
      "input",
      debounce(() => {
        // Just update visual state — actual recompute requires button press
      }, 300)
    );
  });

}());
