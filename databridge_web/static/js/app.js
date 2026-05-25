(function () {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest?.("[data-load-detail-preview]");
    if (!button) return;
    const zone = button.closest("[data-detail-preview-zone]");
    const url = zone?.dataset.detailPreviewUrl;
    if (!zone || !url) return;

    event.preventDefault();
    const previousText = button.textContent;
    button.disabled = true;
    button.textContent = "Chargement de l’aperçu...";
    zone.classList.add("is-loading");

    try {
      const response = await fetch(url, {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const payload = await response.json();
      if (payload.html) {
        zone.innerHTML = payload.html;
      } else {
        zone.innerHTML = '<div class="alert alert-warning app-alert">Impossible de charger l’aperçu pour le moment.</div>';
      }
    } catch (error) {
      zone.innerHTML = '<div class="alert alert-warning app-alert">Impossible de charger l’aperçu pour le moment.</div>';
    } finally {
      zone.classList.remove("is-loading");
      button.textContent = previousText;
    }
  });
})();

(function () {
  const form = document.querySelector("#dataset-create-form");
  if (!form) return;

  const sourceSelect    = form.querySelector("#source_code");
  const topicSelect     = form.querySelector("#topic_id");
  const indicatorSearch = form.querySelector("#indicator_search");
  const indicatorList   = form.querySelector("#indicator-list");
  const indicatorPageInput = form.querySelector("#indicator_page");
  const indicatorPagination = form.querySelector("#indicator-pagination");
  const countrySearch   = form.querySelector("#country_search");
  const countrySelect   = form.querySelector("#country_id");
  const publishButton   = form.querySelector("#generate-links-button") || form.querySelector("#publish-button");
  const existingWrap    = form.querySelector("#existing-dataset-wrap");
  const selectedCount   = form.querySelector("#selected-indicator-count");
  const indicatorLimitCount = form.querySelector("#indicator-limit-count");
  const indicatorLimitHelper = form.querySelector("#indicator-limit-helper");
  const reviewMode      = form.querySelector("#review-mode");
  const reviewSource    = form.querySelector("#review-source");
  const reviewCountry   = form.querySelector("#review-country");
  const reviewIndicators = form.querySelector("#review-indicators");
  const previewZone     = form.querySelector("#preview-async-zone");
  const previewActionSlot = form.querySelector("#preview-action-slot");
  const stepperItems    = Array.from(form.querySelectorAll(".stepper-item"));
  let previewReady      = form.dataset.previewReady === "true";
  let sourceLimits = {};
  try {
    sourceLimits = JSON.parse(form.dataset.sourceLimits || "{}");
  } catch (error) {
    sourceLimits = {};
  }

  const selectedIndicators = new Set(
    Array.from(form.querySelectorAll('input[name="indicator_ids"]:checked')).map((el) => el.value)
  );

  const urls = {
    topics:     form.dataset.topicsUrl,
    indicators: form.dataset.indicatorsUrl,
    countries:  form.dataset.countriesUrl,
    preview:    form.dataset.previewUrl,
  };

  /* ── Utilities ──────────────────────────────────────── */
  function debounce(fn, delay) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&",  "&amp;")
      .replaceAll("<",  "&lt;")
      .replaceAll(">",  "&gt;")
      .replaceAll('"',  "&quot;")
      .replaceAll("'",  "&#039;");
  }

  function spinnerSvg() {
    return `
      <svg style="animation:spin .8s linear infinite" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10" stroke-opacity=".25"/>
        <path d="M12 2a10 10 0 0 1 10 10"/>
      </svg>`;
  }

  function syncPreservedIndicators() {
    form.querySelectorAll('input[data-preserved-indicator="1"]').forEach((el) => el.remove());

    const visibleChecked = new Set(
      Array.from(form.querySelectorAll('input[name="indicator_ids"]:checked')).map((el) => el.value)
    );
    selectedIndicators.forEach((value) => {
      if (visibleChecked.has(value)) return;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "indicator_ids";
      hidden.value = value;
      hidden.dataset.preservedIndicator = "1";
      form.appendChild(hidden);
    });
  }

  function setStepperState(state) {
    stepperItems.forEach((item) => {
      const step = Number(item.dataset.step || 0);
      item.classList.remove("active", "complete", "current", "loading");
      if (step <= 3) item.classList.add("active", "complete");
      if (state === "loading" && step === 4) item.classList.add("active", "current", "loading");
      if (state === "ready" && step === 4) item.classList.add("active", "complete");
      if (state === "ready" && step === 5) item.classList.add("active", "current");
    });
  }

  function renderPreviewButton() {
    if (!previewActionSlot) return;
    previewActionSlot.innerHTML = `
      <button class="btn btn-soft btn-lg" type="submit" name="action" value="preview" data-preview-submit="1">
        Aperçu des données
      </button>`;
  }

  function markPreviewReady(message) {
    previewReady = true;
    form.dataset.previewReady = "true";
    const limitState = updateIndicatorCount();
    if (publishButton) {
      publishButton.disabled = limitState.exceeded;
      if (limitState.exceeded) publishButton.setAttribute("aria-disabled", "true");
      else publishButton.removeAttribute("aria-disabled");
    }
    if (previewActionSlot) {
      previewActionSlot.innerHTML = `
        <span class="preview-ready-pill">
          <span class="preview-pulse"></span>
          ${escapeHtml(message || "Aperçu validé")}
        </span>`;
    }
    setStepperState("ready");
  }

  function invalidatePreview() {
    if (!previewReady) return;
    previewReady = false;
    form.dataset.previewReady = "false";
    if (publishButton) {
      publishButton.disabled = true;
      publishButton.setAttribute("aria-disabled", "true");
    }
    renderPreviewButton();
    setStepperState("draft");
  }

  /* ── State helpers ──────────────────────────────────── */
  function selectedIndicatorValues() {
    form.querySelectorAll('input[name="indicator_ids"]:checked').forEach((el) => selectedIndicators.add(el.value));
    return Array.from(selectedIndicators);
  }

  function currentSourceLimit() {
    const sourceCode = String(sourceSelect.value || "WB").toUpperCase();
    const sourceLimit = sourceLimits[sourceCode] || { max_indicators_per_dataset: 60, label: sourceCode || "Source" };
    return {
      max: Number(sourceLimit.max_indicators_per_dataset || 60),
      label: sourceLimit.label || sourceCode || "Source",
    };
  }

  function setLimitMessage(message, isError) {
    if (!indicatorLimitHelper) return;
    indicatorLimitHelper.textContent = message;
    indicatorLimitHelper.classList.toggle("limit-exceeded", Boolean(isError));
  }

  function updateModeUi() {
    const mode = form.querySelector('input[name="mode"]:checked')?.value || "new";
    existingWrap.style.display = mode === "version" ? "block" : "none";
    if (reviewMode) reviewMode.textContent = mode === "version" ? "Mise à jour d’export" : "Nouveau jeu de données";
  }

  function updateIndicatorCount() {
    const count = selectedIndicatorValues().length;
    const limit = currentSourceLimit();
    if (selectedCount)    selectedCount.textContent    = count;
    if (indicatorLimitCount) indicatorLimitCount.textContent = limit.max;
    if (reviewIndicators) reviewIndicators.textContent = count;
    const exceeded = count > limit.max;
    if (exceeded) {
      setLimitMessage(`${limit.label} autorise au maximum ${limit.max} indicateurs par jeu de données.`, true);
    } else {
      setLimitMessage(`La source ${limit.label} autorise au maximum ${limit.max} indicateurs par jeu de données.`, false);
    }
    if (publishButton && previewReady) {
      publishButton.disabled = exceeded;
      if (exceeded) publishButton.setAttribute("aria-disabled", "true");
      else publishButton.removeAttribute("aria-disabled");
    }
    return { count, limit: limit.max, exceeded };
  }

  function updateReview() {
    if (reviewSource) reviewSource.textContent = sourceSelect.value || "—";
    if (reviewCountry) {
      const opt = countrySelect.options[countrySelect.selectedIndex];
      reviewCountry.textContent = opt && opt.value ? opt.textContent : "—";
    }
    updateIndicatorCount();
  }

  /* ── Fetch helpers ──────────────────────────────────── */
  async function fetchJson(url, params) {
    const query    = new URLSearchParams(params);
    const response = await fetch(`${url}?${query.toString()}`, { headers: { Accept: "application/json" } });
    const data     = await response.json();
    if (!data.ok) throw new Error(data.message || "Chargement impossible.");
    return data;
  }

  async function previewJeuDonnees(button) {
    if (!urls.preview || !previewZone) return false;

    const previousHtml = button ? button.innerHTML : "";
    if (button) {
      button.disabled = true;
      button.innerHTML = `${spinnerSvg()} Construction de l'aperçu...`;
    }
    if (publishButton) {
      publishButton.disabled = true;
      publishButton.setAttribute("aria-disabled", "true");
    }

    setStepperState("loading");
    previewZone.innerHTML = `
      <section class="panel builder-section preview-loading-card">
        <div class="preview-loader-orb">${spinnerSvg()}</div>
        <div>
          <span class="eyebrow">Vérification en cours</span>
          <h3>Construction de l'aperçu réel</h3>
          <p>DataBridge interroge la source, valide les lignes et prépare le tableau sans recharger la page.</p>
        </div>
      </section>`;

    syncPreservedIndicators();
    const formData = new FormData(form);
    formData.set("action", "preview");

    try {
      const response = await fetch(urls.preview, {
        method: "POST",
        body: formData,
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.message || "Aperçu impossible pour cette sélection.");
      }

      previewZone.innerHTML = data.html;
      markPreviewReady(data.message);
      previewZone.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return true;
    } catch (error) {
      previewReady = false;
      form.dataset.previewReady = "false";
      previewZone.innerHTML = `
        <div class="alert alert-warning app-alert preview-error-card">
          <strong>Aperçu non disponible.</strong>
          <span>${escapeHtml(error.message || "Une erreur est survenue.")}</span>
        </div>`;
      setStepperState("draft");
      if (publishButton) {
        publishButton.disabled = true;
        publishButton.setAttribute("aria-disabled", "true");
      }
      if (button) {
        button.disabled = false;
        button.innerHTML = previousHtml;
      }
      return false;
    }
  }

  function setOptions(select, items, options) {
    const currentValue = select.value;
    select.innerHTML = "";
    if (options.placeholder) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = options.placeholder;
      select.appendChild(opt);
    }
    items.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = options.label(item);
      select.appendChild(opt);
    });
    if (Array.from(select.options).some((o) => o.value === currentValue)) {
      select.value = currentValue;
    }
  }

  /* ── Indicator list renderer ────────────────────────── */
  function setLoading(container) {
    container.innerHTML = `
      <div style="padding:20px 14px; color:var(--muted); font-size:13.5px; display:flex; align-items:center; gap:8px;">
        <svg style="animation:spin .8s linear infinite; flex-shrink:0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10" stroke-opacity=".25"/>
          <path d="M12 2a10 10 0 0 1 10 10" />
        </svg>
        Chargement…
      </div>`;
  }

  function renderIndicators(items) {
    indicatorList.innerHTML = "";
    if (!items.length) {
      indicatorList.innerHTML = `
        <div class="empty-state compact">
          <strong>Aucun indicateur trouvé</strong>
          <p>Essayez une autre recherche ou un autre thème.</p>
        </div>`;
      updateIndicatorCount();
      return;
    }
    items.forEach((item) => {
      const label   = document.createElement("label");
      label.className = "indicator-option";
      const checked   = selectedIndicators.has(String(item.id)) ? "checked" : "";
      label.innerHTML = `
        <input type="checkbox" name="indicator_ids" value="${item.id}" ${checked}>
        <span>
          <strong>${escapeHtml(item.code)}</strong>
          <small>${escapeHtml(item.name)}</small>
        </span>`;
      indicatorList.appendChild(label);
    });
    updateIndicatorCount();
  }

  function renderIndicatorPagination(pagination) {
    if (!indicatorPagination) return;
    const page = Math.max(1, Number(pagination.page || 1));
    const hasPrevious = Boolean(pagination.has_previous);
    const hasNext = Boolean(pagination.has_next);
    indicatorPagination.dataset.page = page;
    if (indicatorPageInput) indicatorPageInput.value = page;
    indicatorPagination.innerHTML = `
      <button class="btn btn-soft btn-sm" type="button" data-indicator-page="${Math.max(1, page - 1)}" ${hasPrevious ? "" : "disabled"}>
        Précédent
      </button>
      <span>Page ${page}</span>
      <button class="btn btn-soft btn-sm" type="button" data-indicator-page="${page + 1}" ${hasNext ? "" : "disabled"}>
        Suivant
      </button>`;
  }

  /* ── Async refresh ──────────────────────────────────── */
  async function refreshTopics() {
    const data = await fetchJson(urls.topics, { source_code: sourceSelect.value });
    setOptions(topicSelect, data.items || [], {
      placeholder: "Sélectionner un thème",
      label: (item) => item.name,
    });
  }

  async function refreshIndicators(page) {
    if (!topicSelect.value) {
      indicatorList.innerHTML = `
        <div class="empty-state compact">
          <strong>Sélectionnez un thème</strong>
          <p>Les indicateurs seront affichés après le choix du thème.</p>
        </div>`;
      renderIndicatorPagination({
        page: 1,
        page_size: Number(indicatorPagination?.dataset.pageSize || 50),
        has_previous: false,
        has_next: false,
      });
      updateIndicatorCount();
      return;
    }
    setLoading(indicatorList);
    const requestedPage = Math.max(1, Number(page || indicatorPageInput?.value || 1));
    if (indicatorPageInput) indicatorPageInput.value = requestedPage;
    const pageSize = Number(indicatorPagination?.dataset.pageSize || 50);
    const data = await fetchJson(urls.indicators, {
      source_code: sourceSelect.value,
      topic_id:    topicSelect.value,
      search:      indicatorSearch.value,
      page:        requestedPage,
      page_size:   pageSize,
    });
    renderIndicators(data.items || []);
    renderIndicatorPagination(data.pagination || { page: requestedPage, page_size: pageSize });
  }

  async function refreshCountries() {
    const data = await fetchJson(urls.countries, { search: countrySearch.value });
    setOptions(countrySelect, data.items || [], {
      placeholder: "Sélectionner un pays",
      label: (item) => `${item.name} (${item.code_iso3})`,
    });
    updateReview();
  }

  /* ── Spinner keyframe ───────────────────────────────── */
  (function injectSpinStyle() {
    if (document.querySelector("#rd-spin-style")) return;
    const s = document.createElement("style");
    s.id = "rd-spin-style";
    s.textContent = "@keyframes spin { to { transform: rotate(360deg); } }";
    document.head.appendChild(s);
  })();

  /* ── Event listeners ────────────────────────────────── */
  form.querySelectorAll('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", () => {
      updateModeUi();
      invalidatePreview();
    });
  });

  sourceSelect.addEventListener("change", async () => {
    invalidatePreview();
    selectedIndicators.clear();
    updateReview();
    if (indicatorPageInput) indicatorPageInput.value = 1;
    try { await refreshTopics(); await refreshIndicators(1); } catch (e) { console.warn(e); }
  });

  topicSelect.addEventListener("change", async () => {
    invalidatePreview();
    if (indicatorPageInput) indicatorPageInput.value = 1;
    try { await refreshIndicators(1); } catch (e) { console.warn(e); }
  });

  indicatorSearch.addEventListener("input", debounce(async () => {
    if (indicatorPageInput) indicatorPageInput.value = 1;
    try { await refreshIndicators(1); } catch (e) { console.warn(e); }
  }, 320));

  countrySearch.addEventListener("input", debounce(async () => {
    try { await refreshCountries(); } catch (e) { console.warn(e); }
  }, 320));

  countrySelect.addEventListener("change", () => {
    updateReview();
    invalidatePreview();
  });

  indicatorList.addEventListener("change", (event) => {
    if (!event.target || event.target.name !== "indicator_ids") return;
    if (event.target.checked) {
      const limit = currentSourceLimit();
      if (!selectedIndicators.has(event.target.value) && selectedIndicators.size >= limit.max) {
        event.target.checked = false;
        setLimitMessage(`${limit.label} autorise au maximum ${limit.max} indicateurs par jeu de données.`, true);
        return;
      }
      selectedIndicators.add(event.target.value);
    } else {
      selectedIndicators.delete(event.target.value);
    }
    updateIndicatorCount();
    invalidatePreview();
  });

  indicatorPagination?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-indicator-page]");
    if (!button || button.disabled) return;
    event.preventDefault();
    try { await refreshIndicators(Number(button.dataset.indicatorPage || 1)); } catch (e) { console.warn(e); }
  });

  form.querySelectorAll("#existing_slug, #start_date, #end_date, #title, #description").forEach((input) => {
    input.addEventListener("input", invalidatePreview);
    input.addEventListener("change", invalidatePreview);
  });

  form.addEventListener("submit", (event) => {
    syncPreservedIndicators();

    const submitter = event.submitter;
    const action = submitter?.value || "";
    if (["preview", "generate_links", "publish"].includes(action) && !topicSelect.value) {
      event.preventDefault();
      topicSelect.focus();
      topicSelect.reportValidity?.();
      return;
    }
    const limitState = updateIndicatorCount();
    if (["preview", "generate_links", "publish"].includes(action) && limitState.exceeded) {
      event.preventDefault();
      setLimitMessage(`Limite dépassée : ${limitState.count} / ${limitState.limit} indicateurs sélectionnés.`, true);
      return;
    }
    if (action === "preview") {
      event.preventDefault();
      previewJeuDonnees(submitter);
      return;
    }

    if (submitter?.dataset.confirmSubmit && !window.confirm(submitter.dataset.confirmSubmit)) {
      event.preventDefault();
      return;
    }

    if (!["generate_links", "publish"].includes(action)) return;

    // Disable export button and show feedback
    if (!publishButton) return;
    publishButton.disabled = true;
    publishButton.innerHTML = `${spinnerSvg()} Generation des liens...`;
  });

  /* ── Init ───────────────────────────────────────────── */
  updateModeUi();
  updateReview();
  setStepperState(previewReady ? "ready" : "draft");
})();

(function () {
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-message-dismiss]");
    if (!button) return;
    event.preventDefault();
    button.closest(".glass-message")?.remove();
  });
})();

(function () {
  document.addEventListener("submit", (event) => {
    const button = event.submitter?.closest?.("[data-builder-transfer-submit], [data-assistant-submit]");
    if (!button) return;
    const form = button.form;
    if (form?.dataset.submitting === "1") {
      event.preventDefault();
      return;
    }
    if (form) {
      form.dataset.submitting = "1";
    }
    if (button.name && button.value && form) {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = button.name;
      hidden.value = button.value;
      form.appendChild(hidden);
    }
    form?.querySelectorAll('button[type="submit"]').forEach((submitButton) => {
      submitButton.disabled = true;
    });
    button.textContent = button.dataset.loadingText || "Ouverture de la création...";
  });
})();

(function () {
  document.addEventListener("click", async (event) => {
    const link = event.target.closest?.(".measure-card-panel .app-pagination a");
    if (!link) return;
    const card = link.closest("[data-measure-card]");
    if (!card) return;

    event.preventDefault();
    card.classList.add("is-loading");
    const requestUrl = new URL(window.location.href);
    const targetUrl = new URL(link.href, window.location.href);
    targetUrl.searchParams.forEach((value, key) => {
      requestUrl.searchParams.set(key, value);
    });

    try {
      const response = await fetch(requestUrl.toString(), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const key = card.getAttribute("data-measure-card");
      const nextCard = doc.querySelector(`[data-measure-card="${key}"]`);
      if (!response.ok || !nextCard) {
        window.location.href = link.href;
        return;
      }
      card.replaceWith(nextCard);
      window.history.replaceState(null, "", requestUrl.toString());
    } catch (error) {
      window.location.href = requestUrl.toString();
    }
  });
})();

(function () {
  document.querySelectorAll("[data-chart-image]").forEach((image) => {
    image.addEventListener("error", () => {
      image.closest(".chronology-chart-frame")?.classList.add("is-chart-error");
    });
    image.addEventListener("load", () => {
      image.closest(".chronology-chart-frame")?.classList.remove("is-chart-error");
    });
  });
})();

(function () {
  document.querySelectorAll("[data-range-output]").forEach((input) => {
    const output = document.querySelector(input.dataset.rangeOutput);
    const sync = () => {
      if (output) output.textContent = input.value;
      const value = Number(input.value);
      const min = Number(input.min);
      const max = Number(input.max);
      input.closest(".parameter-field")?.classList.toggle("has-error", value < min || value > max);
    };
    input.addEventListener("input", sync);
    sync();
  });

  const parameterBoard = document.querySelector(".parameter-board[data-ai-model-options]");
  if (parameterBoard) {
    let modelOptions = {};
    try {
      modelOptions = JSON.parse(parameterBoard.dataset.aiModelOptions || "{}");
    } catch (error) {
      modelOptions = {};
    }

    parameterBoard.querySelectorAll("[data-ai-provider-select]").forEach((providerSelect) => {
      providerSelect.addEventListener("change", () => {
        const layer = providerSelect.dataset.aiLayer;
        const modelSelect = parameterBoard.querySelector(`[data-ai-model-select][data-ai-layer="${layer}"]`);
        if (!layer || !modelSelect) return;
        const models = modelOptions?.[layer]?.[providerSelect.value] || [];
        modelSelect.innerHTML = "";
        models.forEach((model) => {
          const option = document.createElement("option");
          option.value = model;
          option.textContent = model;
          modelSelect.appendChild(option);
        });
      });
    });
  }

  async function writeClipboardText(text) {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
      throw new Error("Copie indisponible");
    }
  }

  /* ── Icône SVG utilisée par le presse-papiers ───────────── */
  const COPIER_SVG_HTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="8" y="8" width="10" height="10" rx="1.8" stroke="#ffffff" stroke-width="1.8"/><rect x="5" y="5" width="10" height="10" rx="1.8" stroke="#ffffff" stroke-width="1.8"/></svg>`;
  const CHECK_SVG_HTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  async function copyToClipboard(value, button) {
    const text = String(value || "").trim();
    if (!text || !button) return;

    // Detect button type: icon button has an inline copy icon child.
    const iconSlot = button.querySelector(".copy-icon");
    const isIconButton = Boolean(iconSlot || button.classList.contains("copy-icon-btn"));
    const prevLabel    = button.getAttribute("aria-label") || "";
    const prevTitle    = button.getAttribute("title") || "";
    // For text-only buttons, save the original text before any state change
    const prevText     = isIconButton ? null : button.textContent;

    try {
      await writeClipboardText(text);

      if (isIconButton) {
        // Swap double-rect icon → green checkmark
        (iconSlot || button).innerHTML = CHECK_SVG_HTML;
      } else {
        button.textContent = "Copié !";
      }

      button.classList.add("copied");
      button.setAttribute("aria-label", "Copié !");
      if (prevTitle) button.setAttribute("title", "Copié !");

    } catch (error) {
      console.warn("Clipboard unavailable", error);
      if (isIconButton) {
        button.setAttribute("aria-label", "Erreur de copie");
      } else {
        button.textContent = "Erreur";
      }
    }

    // Auto-reset after 2 s
    window.setTimeout(() => {
      if (isIconButton) {
        // Restore double-rect copy icon
        const currentIconSlot = button.querySelector(".copy-icon");
        (currentIconSlot || button).innerHTML = COPIER_SVG_HTML;
      } else {
        button.textContent = prevText || "Copier";
      }
      button.classList.remove("copied");
      if (prevLabel) button.setAttribute("aria-label", prevLabel);
      if (prevTitle) button.setAttribute("title", prevTitle);
    }, 2000);
  }

  function resolveClipboardValue(button) {
    if (button.dataset.copyValue) return button.dataset.copyValue;

    if (button.dataset.copyTarget) {
      const target = document.querySelector(button.dataset.copyTarget);
      if (!target) return "";
      if ("value" in target) return target.value;
      return target.textContent || "";
    }

    const wrapper = button.closest(".export-link-card, .copy-field-wrap");
    const input = wrapper?.querySelector("input, textarea");
    if (input) return input.value;
    return wrapper?.textContent || "";
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy-value], [data-copy-target]");
    if (!button) return;
    event.preventDefault();
    copyToClipboard(resolveClipboardValue(button), button);
  });
})();
