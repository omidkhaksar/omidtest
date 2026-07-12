const boardEl = document.getElementById("board");
const mobileTabsEl = document.getElementById("mobileTabs");
const boardLoading = document.getElementById("boardLoading");
const summaryEl = document.getElementById("summary");
const pipelineStatsEl = document.getElementById("pipelineStats");
const modalEl = document.getElementById("modal");
const detailModalEl = document.getElementById("detailModal");
const formEl = document.getElementById("listingForm");
const statusSelect = document.getElementById("listingStatus");
const listingUrlInput = document.getElementById("listingUrl");
const listingTitleInput = document.getElementById("listingTitle");
const modalPreview = document.getElementById("modalPreview");
const previewGallery = document.getElementById("previewGallery");
const previewMeta = document.getElementById("previewMeta");

let statuses = [];
let draggedCardId = null;
let previewTimer = null;
let titleEditedManually = false;
let activeListing = null;

const HIGHLIGHT_SPECS = ["متراژ", "اتاق", "طبقه", "ساخت", "قیمت کل", "قیمت هر متر", "ودیعه", "اجاره"];

const STATUS_LABELS = {
  new: "جدید",
  need_call: "تماس بگیر",
  no_answer: "جواب نداد",
  waitlist: "لیست انتظار",
  in_talk: "در حال پیگیری",
  rejected: "رد شد",
  bought: "خریدم",
};

const STATUS_META = {
  new: { step: 1, icon: "✦", desc: "تازه از دیوار" },
  need_call: { step: 2, icon: "📞", desc: "باید تماس بگیرم" },
  no_answer: { step: 3, icon: "🔇", desc: "جواب نداد" },
  waitlist: { step: 4, icon: "⏳", desc: "برای بعد نگه می‌دارم" },
  in_talk: { step: 5, icon: "💬", desc: "بازدید و مذاکره" },
  rejected: { step: 6, icon: "✕", desc: "مناسب نبود" },
  bought: { step: 7, icon: "🔑", desc: "خریدم!" },
};

const STATUS_THEME = {
  new: "#8b9cb8",
  need_call: "#5b9cf5",
  no_answer: "#f5b942",
  waitlist: "#b07cf5",
  in_talk: "#5dd879",
  rejected: "#f56b6b",
  bought: "#3ecf9a",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  if (response.status === 204) return null;
  return response.json();
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString("fa-IR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(status, apiLabel) {
  return STATUS_LABELS[status] || apiLabel || status;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function looksLikeUrl(value) {
  return /^https?:\/\//i.test(value.trim());
}

function renderSpecChips(specs, limit = 4) {
  const entries = [];
  for (const key of HIGHLIGHT_SPECS) {
    if (specs[key]) entries.push([key, specs[key]]);
  }
  for (const [key, value] of Object.entries(specs || {})) {
    if (!HIGHLIGHT_SPECS.includes(key)) entries.push([key, value]);
  }
  return entries
    .slice(0, limit)
    .map(([key, value]) => `<span class="spec-chip">${escapeHtml(key)} ${escapeHtml(value)}</span>`)
    .join("");
}

function setupImageGallery(container, images, title = "") {
  if (!container) return;
  const list = (images || []).filter(Boolean);
  if (!list.length) {
    container.innerHTML = `<div class="gallery-empty">عکسی نیست</div>`;
    return;
  }

  let index = 0;
  container.innerHTML = "";

  const viewer = document.createElement("div");
  viewer.className = "gallery-viewer";

  const mainImg = document.createElement("img");
  mainImg.className = "gallery-main";
  mainImg.alt = title;
  mainImg.loading = "eager";

  const counter = document.createElement("span");
  counter.className = "gallery-counter";

  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "gallery-nav gallery-prev";
  prevBtn.setAttribute("aria-label", "عکس قبلی");
  prevBtn.textContent = "‹";

  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "gallery-nav gallery-next";
  nextBtn.setAttribute("aria-label", "عکس بعدی");
  nextBtn.textContent = "›";

  const thumbs = document.createElement("div");
  thumbs.className = "gallery-thumbs";
  thumbs.setAttribute("role", "tablist");
  thumbs.setAttribute("aria-label", "تصاویر آگهی");

  function update(i) {
    index = (i + list.length) % list.length;
    mainImg.src = list[index];
    counter.textContent = `${index + 1} / ${list.length}`;
    const multi = list.length > 1;
    prevBtn.hidden = !multi;
    nextBtn.hidden = !multi;
    counter.hidden = !multi;
    thumbs.querySelectorAll(".gallery-thumb").forEach((el, j) => {
      el.classList.toggle("active", j === index);
      el.setAttribute("aria-selected", j === index ? "true" : "false");
      if (j === index) {
        el.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
      }
    });
  }

  list.forEach((src, i) => {
    const thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = "gallery-thumb";
    thumb.setAttribute("role", "tab");
    thumb.setAttribute("aria-label", `عکس ${i + 1}`);
    thumb.innerHTML = `<img src="${escapeHtml(src)}" alt="" loading="lazy" />`;
    thumb.addEventListener("click", () => update(i));
    thumbs.appendChild(thumb);
  });

  prevBtn.addEventListener("click", () => update(index - 1));
  nextBtn.addEventListener("click", () => update(index + 1));

  let touchStartX = 0;
  viewer.addEventListener(
    "touchstart",
    (e) => {
      touchStartX = e.touches[0].clientX;
    },
    { passive: true }
  );
  viewer.addEventListener(
    "touchend",
    (e) => {
      if (list.length < 2) return;
      const dx = e.changedTouches[0].clientX - touchStartX;
      if (Math.abs(dx) > 40) update(dx > 0 ? index - 1 : index + 1);
    },
    { passive: true }
  );

  viewer.append(prevBtn, mainImg, nextBtn, counter);
  container.appendChild(viewer);
  if (list.length > 1) container.appendChild(thumbs);
  update(0);
}

function showPreview(data) {
  if (!data) {
    modalPreview.classList.remove("visible");
    return;
  }

  const images = data.images?.length ? data.images : data.image_url ? [data.image_url] : [];
  if (!images.length) {
    modalPreview.classList.remove("visible");
    return;
  }

  setupImageGallery(previewGallery, images, data.title || "پیش‌نمایش آگهی");
  const parts = [data.price, data.location || [data.district, data.city].filter(Boolean).join("، ")].filter(Boolean);
  previewMeta.innerHTML = `
    <div>${escapeHtml(parts.join(" · "))}</div>
    <div class="preview-specs">${renderSpecChips(data.specs || {}, 6)}</div>
    ${data.description ? `<div class="preview-description">${escapeHtml(data.description)}</div>` : ""}
  `;
  modalPreview.classList.add("visible");
}

async function previewFromUrl(url, { forceTitle = false } = {}) {
  const trimmed = url.trim();
  if (!looksLikeUrl(trimmed)) {
    modalPreview.classList.remove("visible");
    return;
  }

  try {
    const data = await api(`/api/preview?url=${encodeURIComponent(trimmed)}`);
    if ((forceTitle || !titleEditedManually) && data.title) {
      listingTitleInput.value = data.title;
    }
    showPreview(data);
  } catch {
    modalPreview.classList.remove("visible");
  }
}

function schedulePreview(url) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => previewFromUrl(url), 350);
}

function renderPipelineStats(columns, total) {
  pipelineStatsEl.innerHTML = columns
    .map((col) => {
      const meta = STATUS_META[col.status] || { step: "?", icon: "•" };
      return `
        <button type="button" class="stat-chip" data-status="${col.status}">
          <span class="stat-step" style="background:${STATUS_THEME[col.status]}">${meta.step}</span>
          <span>${escapeHtml(statusLabel(col.status, col.label))}</span>
          <strong>${col.listings.length}</strong>
        </button>
      `;
    })
    .join("");

  pipelineStatsEl.querySelectorAll(".stat-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const status = chip.dataset.status;
      const col = boardEl.querySelector(`.column[data-status="${status}"]`);
      if (col) col.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
      mobileTabsEl?.querySelectorAll(".mobile-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.status === status);
      });
    });
  });

  const active = columns.reduce((n, c) => {
    if (c.status === "rejected" || c.status === "bought") return n;
    return n + c.listings.length;
  }, 0);

  const summaryText = summaryEl.querySelector(".summary-text") || summaryEl;
  summaryText.textContent = `${total} آگهی · ${active} فعال`;
}

function openDetailModal(listing) {
  activeListing = listing;
  document.getElementById("detailTitle").textContent = listing.title;
  document.getElementById("detailStatusLabel").textContent = statusLabel(listing.status, listing.status_label);
  document.getElementById("detailStatusLabel").style.color = STATUS_THEME[listing.status] || "var(--gold)";
  document.getElementById("detailStatusLabel").style.borderColor = `${STATUS_THEME[listing.status] || "#f0b45a"}44`;
  document.getElementById("detailStatusLabel").style.background = `${STATUS_THEME[listing.status] || "#f0b45a"}18`;

  document.getElementById("detailPriceLocation").innerHTML = `
    ${listing.price ? `<div class="card-price">${escapeHtml(listing.price)}</div>` : ""}
    ${listing.location ? `<div class="card-location" style="margin-top:0.35rem">${escapeHtml(listing.location)}</div>` : ""}
  `;

  const specsEl = document.getElementById("detailSpecs");
  const specs = listing.specs || {};
  specsEl.innerHTML = Object.entries(specs)
    .map(
      ([key, value]) => `
        <div class="spec-item">
          <strong>${escapeHtml(key)}</strong>
          <span>${escapeHtml(value)}</span>
        </div>
      `
    )
    .join("") || '<div class="empty"><span class="empty-icon">📋</span>مشخصاتی نیست</div>';

  document.getElementById("detailDescription").textContent = listing.description || "توضیحاتی ثبت نشده.";
  document.getElementById("detailNotes").textContent = listing.notes || "یادداشتی نیست — شماره تماس، زمان بازدید یا دلیل رد را بنویسید.";

  const gallery = document.getElementById("detailGallery");
  const images = listing.images?.length ? listing.images : listing.image_url ? [listing.image_url] : [];
  setupImageGallery(gallery, images, listing.title || "آگهی");

  const tagsBlock = document.getElementById("detailTagsBlock");
  const tagsEl = document.getElementById("detailTags");
  if (listing.tags?.length) {
    tagsBlock.style.display = "block";
    tagsEl.innerHTML = listing.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  } else {
    tagsBlock.style.display = "none";
    tagsEl.innerHTML = "";
  }

  document.getElementById("detailOpenLink").href = listing.url;
  detailModalEl.classList.add("open");
  setBodyScrollLocked(true);
}

function closeDetailModal() {
  detailModalEl.classList.remove("open");
  activeListing = null;
  setBodyScrollLocked(modalEl.classList.contains("open"));
}

function renderCard(listing, index = 0) {
  const card = document.createElement("article");
  card.className = `card status-${listing.status}`;
  card.draggable = true;
  card.dataset.id = listing.id;
  card.style.setProperty("--i", index);
  card.style.setProperty("--col-accent", STATUS_THEME[listing.status] || STATUS_THEME.new);

  const images = listing.images?.length ? listing.images : listing.image_url ? [listing.image_url] : [];
  const imageBlock = images.length
    ? `<div class="card-image-wrap">
         <span class="status-ribbon">${escapeHtml(statusLabel(listing.status, listing.status_label))}</span>
         <img class="card-image" src="${escapeHtml(images[0])}" alt="${escapeHtml(listing.title)}" loading="lazy" />
         ${listing.price ? `<span class="card-price-overlay">${escapeHtml(listing.price)}</span>` : ""}
         ${images.length > 1 ? `<span class="image-count">${images.length} عکس</span>` : ""}
       </div>`
    : `<span class="status-ribbon card-ribbon-only">${escapeHtml(statusLabel(listing.status, listing.status_label))}</span>`;

  card.innerHTML = `
    ${imageBlock}
    <div class="card-body">
      <div class="card-title" dir="auto">${escapeHtml(listing.title)}</div>
      ${!images.length && listing.price ? `<div class="card-price">${escapeHtml(listing.price)}</div>` : ""}
      ${listing.location ? `<div class="card-location" dir="auto">${escapeHtml(listing.location)}</div>` : ""}
      <div class="card-specs">${renderSpecChips(listing.specs || {}, 2)}</div>
      <div class="card-tap-hint">برای جزئیات بزنید · بکشید برای جابجایی</div>
    </div>
  `;

  card.addEventListener("dragstart", (e) => {
    draggedCardId = listing.id;
    card.classList.add("dragging");
  });

  card.addEventListener("dragend", () => {
    draggedCardId = null;
    card.classList.remove("dragging");
  });

  card.addEventListener("click", (event) => {
    if (card.classList.contains("dragging")) return;
    openDetailModal(listing);
  });

  return card;
}

function renderMobileTabs(columns) {
  if (!mobileTabsEl) return;
  mobileTabsEl.innerHTML = columns
    .map((col, i) => {
      const meta = STATUS_META[col.status] || { step: "?", icon: "•" };
      return `
        <button type="button" class="mobile-tab${i === 0 ? " active" : ""}" data-status="${col.status}">
          <span class="mobile-tab-step" style="background:${STATUS_THEME[col.status]}">${meta.step}</span>
          <span class="mobile-tab-label">${escapeHtml(statusLabel(col.status, col.label))}</span>
          <span class="mobile-tab-count">${col.listings.length}</span>
        </button>
      `;
    })
    .join("");

  mobileTabsEl.querySelectorAll(".mobile-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      mobileTabsEl.querySelectorAll(".mobile-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const col = boardEl.querySelector(`.column[data-status="${tab.dataset.status}"]`);
      if (col) col.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
    });
  });
}

function setBodyScrollLocked(locked) {
  document.body.classList.toggle("modal-open", locked);
}

function renderColumn(column) {
  const col = document.createElement("section");
  const meta = STATUS_META[column.status] || { step: "?", icon: "•", desc: "" };
  col.className = `column status-${column.status}`;
  col.dataset.status = column.status;
  col.style.setProperty("--col-accent", STATUS_THEME[column.status]);

  col.innerHTML = `
    <div class="column-header">
      <div class="column-title-wrap">
        <span class="column-step">${meta.step}</span>
        <div class="column-text">
          <span class="column-title">${meta.icon} ${escapeHtml(statusLabel(column.status, column.label))}</span>
          <span class="column-desc">${escapeHtml(meta.desc)}</span>
        </div>
      </div>
      <span class="count">${column.listings.length}</span>
    </div>
    <div class="column-body"></div>
  `;

  const body = col.querySelector(".column-body");

  body.addEventListener("dragover", (event) => {
    event.preventDefault();
    body.classList.add("drag-over");
  });

  body.addEventListener("dragleave", () => body.classList.remove("drag-over"));

  body.addEventListener("drop", async (event) => {
    event.preventDefault();
    body.classList.remove("drag-over");
    if (!draggedCardId) return;
    await api(`/api/listings/${draggedCardId}/move`, {
      method: "POST",
      body: JSON.stringify({ status: col.dataset.status }),
    });
    await loadBoard();
  });

  if (!column.listings.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.innerHTML = '<span class="empty-icon">＋</span>آگهی بکشید اینجا<br><small>یا از تلگرام لینک بفرستید</small>';
    body.appendChild(empty);
  } else {
    column.listings.forEach((listing, i) => body.appendChild(renderCard(listing, i)));
  }

  return col;
}

async function loadMeta() {
  const meta = await api("/api/meta");
  statuses = meta.statuses;
  statusSelect.innerHTML = statuses
    .map((s) => `<option value="${s.value}">${escapeHtml(statusLabel(s.value, s.label))}</option>`)
    .join("");
}

async function loadBoard() {
  const data = await api("/api/kanban");
  boardLoading?.classList.add("hidden");
  boardEl.innerHTML = "";
  data.columns.forEach((column) => boardEl.appendChild(renderColumn(column)));
  renderPipelineStats(data.columns, data.total);
  renderMobileTabs(data.columns);
}

function openModal(listing = null) {
  titleEditedManually = false;
  document.getElementById("modalTitle").textContent = listing ? "ویرایش آگهی" : "افزودن آگهی";
  document.getElementById("listingId").value = listing ? listing.id : "";
  listingTitleInput.value = listing ? listing.title : "";
  listingUrlInput.value = listing ? listing.url : "";
  document.getElementById("listingNotes").value = listing ? listing.notes : "";
  document.getElementById("listingStatus").value = listing ? listing.status : "new";

  if (listing) showPreview(listing);
  else modalPreview.classList.remove("visible");

  closeDetailModal();
  modalEl.classList.add("open");
  setBodyScrollLocked(true);
}

function closeModal() {
  modalEl.classList.remove("open");
  formEl.reset();
  modalPreview.classList.remove("visible");
  titleEditedManually = false;
  setBodyScrollLocked(detailModalEl.classList.contains("open"));
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.getElementById("listingId").value;
  const payload = {
    title: listingTitleInput.value.trim(),
    url: listingUrlInput.value.trim(),
    notes: document.getElementById("listingNotes").value.trim(),
    status: document.getElementById("listingStatus").value,
  };

  if (id) {
    await api(`/api/listings/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  } else {
    await api("/api/listings", { method: "POST", body: JSON.stringify(payload) });
  }

  closeModal();
  await loadBoard();
});

listingUrlInput.addEventListener("input", (e) => schedulePreview(e.target.value));
listingUrlInput.addEventListener("blur", (e) => previewFromUrl(e.target.value, { forceTitle: true }));
listingTitleInput.addEventListener("input", () => { titleEditedManually = true; });

document.getElementById("addBtn").addEventListener("click", () => openModal());
document.getElementById("fabAdd")?.addEventListener("click", () => openModal());
document.getElementById("cancelBtn").addEventListener("click", closeModal);
document.querySelectorAll("[data-close-modal]").forEach((el) => el.addEventListener("click", closeModal));
document.getElementById("refreshBtn").addEventListener("click", loadBoard);
document.getElementById("detailCloseBtn").addEventListener("click", closeDetailModal);
document.getElementById("detailEditBtn").addEventListener("click", () => {
  if (activeListing) openModal(activeListing);
});
document.getElementById("detailRefreshBtn").addEventListener("click", async () => {
  if (!activeListing) return;
  const refreshed = await api(`/api/listings/${activeListing.id}/refresh`, { method: "POST" });
  activeListing = refreshed;
  openDetailModal(refreshed);
  await loadBoard();
});
document.getElementById("detailDeleteBtn").addEventListener("click", async () => {
  if (!activeListing) return;
  if (!confirm("این آگهی حذف شود؟")) return;
  await api(`/api/listings/${activeListing.id}`, { method: "DELETE" });
  closeDetailModal();
  await loadBoard();
});
detailModalEl.addEventListener("click", (e) => { if (e.target === detailModalEl) closeDetailModal(); });
modalEl.addEventListener("click", (e) => { if (e.target === modalEl) closeModal(); });

(async function init() {
  await loadMeta();
  await loadBoard();
  setInterval(loadBoard, 15000);
})();
