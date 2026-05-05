(function () {
  const form = document.querySelector("#dataset-create-form");
  if (!form) return;

  const sourceSelect    = form.querySelector("#source_code");
  const topicSelect     = form.querySelector("#topic_id");
  const indicatorSearch = form.querySelector("#indicator_search");
  const indicatorList   = form.querySelector("#indicator-list");
  const countrySearch   = form.querySelector("#country_search");
  const countrySelect   = form.querySelector("#country_id");
  const publishButton   = form.querySelector("#publish-button");
  const existingWrap    = form.querySelector("#existing-dataset-wrap");
  const selectedCount   = form.querySelector("#selected-indicator-count");
  const reviewMode      = form.querySelector("#review-mode");
  const reviewSource    = form.querySelector("#review-source");
  const reviewCountry   = form.querySelector("#review-country");
  const reviewIndicators = form.querySelector("#review-indicators");

  const selectedIndicators = new Set(
    Array.from(form.querySelectorAll('input[name="indicator_ids"]:checked')).map((el) => el.value)
  );

  const urls = {
    topics:     form.dataset.topicsUrl,
    indicators: form.dataset.indicatorsUrl,
    countries:  form.dataset.countriesUrl,
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

  /* ── State helpers ──────────────────────────────────── */
  function selectedIndicatorValues() {
    form.querySelectorAll('input[name="indicator_ids"]:checked').forEach((el) => selectedIndicators.add(el.value));
    return Array.from(selectedIndicators);
  }

  function updateModeUi() {
    const mode = form.querySelector('input[name="mode"]:checked')?.value || "new";
    existingWrap.style.display = mode === "version" ? "block" : "none";
    if (reviewMode) reviewMode.textContent = mode === "version" ? "Nouvelle version" : "Nouveau dataset";
  }

  function updateIndicatorCount() {
    const count = selectedIndicatorValues().length;
    if (selectedCount)    selectedCount.textContent    = count;
    if (reviewIndicators) reviewIndicators.textContent = count;
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
    return data.items || [];
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
      const unitHtml  = item.unit
        ? `<small>${escapeHtml(item.name)} — ${escapeHtml(item.unit)}</small>`
        : `<small>${escapeHtml(item.name)}</small>`;
      label.innerHTML = `
        <input type="checkbox" name="indicator_ids" value="${item.id}" ${checked}>
        <span>
          <strong>${escapeHtml(item.code)}</strong>
          ${unitHtml}
        </span>`;
      indicatorList.appendChild(label);
    });
    updateIndicatorCount();
  }

  /* ── Async refresh ──────────────────────────────────── */
  async function refreshTopics() {
    const items = await fetchJson(urls.topics, { source_code: sourceSelect.value });
    setOptions(topicSelect, items, {
      placeholder: "Tous les thèmes",
      label: (item) => item.name,
    });
  }

  async function refreshIndicators() {
    setLoading(indicatorList);
    const items = await fetchJson(urls.indicators, {
      source_code: sourceSelect.value,
      topic_id:    topicSelect.value,
      search:      indicatorSearch.value,
    });
    renderIndicators(items);
  }

  async function refreshCountries() {
    const items = await fetchJson(urls.countries, { search: countrySearch.value });
    setOptions(countrySelect, items, {
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
    input.addEventListener("change", updateModeUi);
  });

  sourceSelect.addEventListener("change", async () => {
    updateReview();
    try { await refreshTopics(); await refreshIndicators(); } catch (e) { console.warn(e); }
  });

  topicSelect.addEventListener("change", async () => {
    try { await refreshIndicators(); } catch (e) { console.warn(e); }
  });

  indicatorSearch.addEventListener("input", debounce(async () => {
    try { await refreshIndicators(); } catch (e) { console.warn(e); }
  }, 320));

  countrySearch.addEventListener("input", debounce(async () => {
    try { await refreshCountries(); } catch (e) { console.warn(e); }
  }, 320));

  countrySelect.addEventListener("change", updateReview);

  indicatorList.addEventListener("change", (event) => {
    if (!event.target || event.target.name !== "indicator_ids") return;
    if (event.target.checked) {
      selectedIndicators.add(event.target.value);
    } else {
      selectedIndicators.delete(event.target.value);
    }
    updateIndicatorCount();
  });

  form.addEventListener("submit", () => {
    // Remove previously injected hidden inputs
    form.querySelectorAll('input[data-preserved-indicator="1"]').forEach((el) => el.remove());

    // Re-inject indicators that are selected but not currently visible
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

    // Disable publish button and show feedback
    if (!publishButton) return;
    publishButton.disabled = true;
    publishButton.innerHTML = `
      <svg style="animation:spin .8s linear infinite" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10" stroke-opacity=".25"/>
        <path d="M12 2a10 10 0 0 1 10 10"/>
      </svg>
      Publication en cours…`;
  });

  /* ── Init ───────────────────────────────────────────── */
  updateModeUi();
  updateReview();
})();