// ---------------------------------------------------------------------------
// ZFS Tool – Frontend Application
// ---------------------------------------------------------------------------

let _csrfToken = sessionStorage.getItem("csrf_token") || "";

const API = {
    async _handle(r) {
        if (r.status === 401) { window.location.href = "/login"; throw new Error("Not authenticated"); }
        if (r.status === 403) { throw new Error("CSRF token invalid – please reload the page"); }
        return r.json();
    },
    _headers(extra) {
        return { "Content-Type": "application/json", "X-CSRF-Token": _csrfToken, ...extra };
    },
    async get(url) {
        const r = await fetch(url);
        return this._handle(r);
    },
    async post(url, data) {
        const r = await fetch(url, {
            method: "POST",
            headers: this._headers(),
            body: JSON.stringify(data),
        });
        return this._handle(r);
    },
    async del(url, data) {
        const r = await fetch(url, {
            method: "DELETE",
            headers: this._headers(),
            body: JSON.stringify(data),
        });
        return this._handle(r);
    },
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentHost = null;
let currentView = "home";

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function toast(msg, type = "info") {
    const c = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function navigate(view) {
    currentView = view;
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    const el = document.querySelector(`[data-view="${view}"]`);
    if (el) el.classList.add("active");
    renderView();
}

function requireHost() {
    if (!currentHost) {
        toast(t("select_host_first"), "error");
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") el.className = v;
        else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
        else el.setAttribute(k, v);
    }
    for (const c of (Array.isArray(children) ? children : [children])) {
        if (typeof c === "string") el.appendChild(document.createTextNode(c));
        else if (c) el.appendChild(c);
    }
    return el;
}

function healthBadge(status) {
    const s = (status || "").toUpperCase();
    let cls = "badge-online";
    if (s === "DEGRADED") cls = "badge-degraded";
    else if (s !== "ONLINE" && s !== "HEALTHY") cls = "badge-offline";
    return h("span", { className: `badge ${cls}` }, s);
}

function statusBadge(status) {
    const s = (status || "").toLowerCase();
    const cls = s === "running" ? "badge-running" : "badge-stopped";
    return h("span", { className: `badge ${cls}` }, status);
}

function setContent(html) {
    document.getElementById("main-view").innerHTML = "";
    if (typeof html === "string") {
        document.getElementById("main-view").innerHTML = html;
    } else {
        document.getElementById("main-view").appendChild(html);
    }
}

function loading() {
    return `<div class="loading-placeholder"><span class="spinner"></span> ${t("loading")}</div>`;
}

// ---------------------------------------------------------------------------
// i18n: Update sidebar labels
// ---------------------------------------------------------------------------
function updateSidebarLanguage() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.dataset.i18n;
        el.textContent = t(key);
    });
    // Update host selector placeholder
    const hostSel = document.getElementById("host-select");
    if (hostSel && hostSel.options.length > 0) {
        hostSel.options[0].textContent = t("select_host");
    }
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

async function renderView() {
    const map = {
        home: viewHome,
        hosts: viewHosts,
        pools: viewPools,
        datasets: viewDatasets,
        snapshots: viewSnapshots,
        "snapshot-check": viewSnapshotCheck,
        replication: viewReplication,
        guests: viewGuests,
        health: viewHealth,
        metrics: viewMetrics,
        audit: viewAudit,
        notifications: viewNotifications,
        ai: viewAI,
    };
    const fn = map[currentView] || viewHome;
    await fn();
}

// -- Home ------------------------------------------------------------------
async function viewHome() {
    setContent(loading());
    const key = await API.get("/api/public-key");
    const hosts = await API.get("/api/hosts");

    const container = h("div");

    // Header
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("home_title")),
        h("p", {}, t("home_subtitle")),
    ]));

    // SSH Key
    const keyCard = h("div", { className: "card" });
    keyCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("ssh_public_key")),
        h("div", { style: "display:flex;gap:6px" }, [
            h("button", { className: "btn btn-sm btn-warning", onClick: () => rotateSshKey() }, t("ssh_rotate_btn")),
            h("button", { className: "btn btn-sm btn-primary", onClick: () => copyKey(key.key) }, t("copy")),
        ]),
    ]));
    const keyBody = h("div", { className: "card-body" });
    if (key.key) {
        const pre = h("div", { className: "key-display", id: "ssh-key-display" }, key.key);
        keyBody.appendChild(pre);
        keyBody.appendChild(h("p", {
            style: "margin-top:10px;font-size:13px;color:var(--text-secondary)"
        }, t("ssh_key_hint")));
    } else {
        keyBody.appendChild(h("p", {}, t("no_ssh_key")));
    }
    keyCard.appendChild(keyBody);
    container.appendChild(keyCard);

    // Live dashboard (replaces the old hardcoded stat tiles)
    if (hosts.length > 0) {
        const dashMount = h("div", { style: "margin-top:16px" }, loading());
        container.appendChild(dashMount);
        // Async — don't block rest of page
        (async () => {
            try {
                const d = await API.get("/api/dashboard");
                dashMount.innerHTML = "";
                dashMount.appendChild(_renderDashboard(d));
            } catch (e) {
                dashMount.innerHTML = `<p class="muted">${escapeHtml(e.message || "Dashboard load failed")}</p>`;
            }
        })();
    }

    // Feature overview
    const featureCard = h("div", { className: "card", style: "margin-top:16px" });
    featureCard.appendChild(h("div", { className: "card-header" }, t("features")));
    const featureBody = h("div", { className: "card-body" });
    featureBody.innerHTML = `
        <div class="grid grid-3" style="gap:20px">
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_pools_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_pools_1"))}</li>
                    <li>${escapeHtml(t("feat_pools_2"))}</li>
                    <li>${escapeHtml(t("feat_pools_3"))}</li>
                    <li>${escapeHtml(t("feat_pools_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_snaps_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_snaps_1"))}</li>
                    <li>${escapeHtml(t("feat_snaps_2"))}</li>
                    <li>${escapeHtml(t("feat_snaps_3"))}</li>
                    <li>${escapeHtml(t("feat_snaps_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_pve_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_pve_1"))}</li>
                    <li>${escapeHtml(t("feat_pve_2"))}</li>
                    <li>${escapeHtml(t("feat_pve_3"))}</li>
                    <li>${escapeHtml(t("feat_pve_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_repl_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_repl_1"))}</li>
                    <li>${escapeHtml(t("feat_repl_2"))}</li>
                    <li>${escapeHtml(t("feat_repl_3"))}</li>
                    <li>${escapeHtml(t("feat_repl_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_health_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_health_1"))}</li>
                    <li>${escapeHtml(t("feat_health_2"))}</li>
                    <li>${escapeHtml(t("feat_health_3"))}</li>
                    <li>${escapeHtml(t("feat_health_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_notify_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_notify_1"))}</li>
                    <li>${escapeHtml(t("feat_notify_2"))}</li>
                    <li>${escapeHtml(t("feat_notify_3"))}</li>
                    <li>${escapeHtml(t("feat_notify_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_ai_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_ai_1"))}</li>
                    <li>${escapeHtml(t("feat_ai_2"))}</li>
                    <li>${escapeHtml(t("feat_ai_3"))}</li>
                    <li>${escapeHtml(t("feat_ai_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_monitor_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_monitor_1"))}</li>
                    <li>${escapeHtml(t("feat_monitor_2"))}</li>
                    <li>${escapeHtml(t("feat_monitor_3"))}</li>
                    <li>${escapeHtml(t("feat_monitor_4"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_auth_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_auth_1"))}</li>
                    <li>${escapeHtml(t("feat_auth_2"))}</li>
                    <li>${escapeHtml(t("feat_auth_3"))}</li>
                </ul>
            </div>
        </div>
    `;
    featureCard.appendChild(featureBody);
    container.appendChild(featureCard);

    // Setup guide
    const setupCard = h("div", { className: "card", style: "margin-top:16px" });
    setupCard.appendChild(h("div", { className: "card-header" }, t("quick_setup")));
    const setupBody = h("div", { className: "card-body" });
    setupBody.innerHTML = `
        <ol style="font-size:13px;color:var(--text-secondary);line-height:2;padding-left:18px">
            <li>${t("setup_1")}</li>
            <li>${t("setup_2")}</li>
            <li>${t("setup_3")}</li>
            <li>${t("setup_4")}</li>
            <li>${t("setup_5")}</li>
        </ol>
    `;
    setupCard.appendChild(setupBody);
    container.appendChild(setupCard);

    setContent(container);
}

function makeStatCard(label, value, extra) {
    const card = h("div", { className: "stat-card" });
    card.appendChild(h("div", { className: "stat-label" }, label));
    card.appendChild(h("div", { className: "stat-value" }, String(value)));
    if (extra) card.appendChild(h("div", { style: "font-size:12px;color:var(--text-secondary);margin-top:4px" }, extra));
    return card;
}

function _renderDashboard(d) {
    const root = document.createElement("div");
    const agg = d.aggregate || {};

    // Aggregate tiles
    const tiles = h("div", { className: "grid grid-4", style: "gap:12px;margin-bottom:16px" });
    const tile = (label, value, sub, ok) => {
        const card = h("div", { className: "stat-card" });
        card.appendChild(h("div", { className: "stat-label" }, label));
        card.appendChild(h("div", {
            className: "stat-value",
            style: ok === false ? "color:var(--error,#f44336)" : (ok === true ? "color:var(--success,#4caf50)" : ""),
        }, String(value)));
        if (sub) card.appendChild(h("div", { style: "font-size:12px;color:var(--text-secondary);margin-top:4px" }, sub));
        return card;
    };
    tiles.appendChild(tile(t("dash_hosts"),
        `${agg.hosts_online || 0} / ${(agg.hosts_online || 0) + (agg.hosts_offline || 0)}`,
        t("dash_hosts_online"),
        (agg.hosts_offline || 0) === 0 ? true : false));
    tiles.appendChild(tile(t("dash_pools"),
        `${agg.pools_ok || 0} / ${agg.pools_total || 0}`,
        t("dash_pools_ok"),
        (agg.pools_degraded || 0) === 0 ? true : false));
    tiles.appendChild(tile(t("dash_capacity_warn"),
        String(agg.pools_capacity_warn || 0),
        t("dash_capacity_warn_sub"),
        (agg.pools_capacity_warn || 0) === 0 ? true : ((agg.pools_capacity_warn || 0) > 0 ? false : undefined)));
    tiles.appendChild(tile(t("dash_forecast_critical"),
        String(agg.forecast_pools_critical || 0),
        t("dash_forecast_critical_sub"),
        (agg.forecast_pools_critical || 0) === 0 ? true : false));
    root.appendChild(tiles);

    const tiles2 = h("div", { className: "grid grid-3", style: "gap:12px;margin-bottom:16px" });
    tiles2.appendChild(tile(t("dash_stale_labels"),
        String(agg.stale_snap_labels || 0),
        t("dash_stale_labels_sub"),
        (agg.stale_snap_labels || 0) === 0 ? true : false));
    tiles2.appendChild(tile(t("dash_audit_failures"),
        String(agg.recent_audit_failures_24h || 0),
        t("dash_audit_failures_sub"),
        (agg.recent_audit_failures_24h || 0) === 0 ? true : false));
    tiles2.appendChild(tile(t("dash_pools_degraded"),
        String(agg.pools_degraded || 0),
        "",
        (agg.pools_degraded || 0) === 0 ? true : false));
    root.appendChild(tiles2);

    // Per-host breakdown
    const hostsCard = h("div", { className: "card" });
    hostsCard.appendChild(h("div", { className: "card-header" }, t("dash_per_host")));
    const tbl = document.createElement("table");
    tbl.innerHTML = `<thead><tr>
        <th>${escapeHtml(t("name"))}</th>
        <th>${escapeHtml(t("status"))}</th>
        <th>${escapeHtml(t("dash_pool"))}</th>
        <th>${escapeHtml(t("dash_health"))}</th>
        <th>${escapeHtml(t("dash_cap"))}</th>
        <th>${escapeHtml(t("dash_free"))}</th>
        <th>${escapeHtml(t("dash_forecast"))}</th>
    </tr></thead>`;
    const tb = document.createElement("tbody");
    for (const host of (d.hosts || [])) {
        const pools = host.pools || [];
        if (pools.length === 0) {
            const tr = document.createElement("tr");
            const statusBadge = host.reachable === true ? `<span class="badge badge-online">${escapeHtml(t("online"))}</span>`
                : host.reachable === false ? `<span class="badge badge-offline">${escapeHtml(t("offline"))}</span>`
                : `<span class="badge badge-stopped">${escapeHtml(t("unknown"))}</span>`;
            tr.innerHTML = `<td>${escapeHtml(host.name)}</td>
                <td>${statusBadge}</td>
                <td colspan="5" class="muted">${escapeHtml(t("dash_no_samples"))}</td>`;
            tb.appendChild(tr);
            continue;
        }
        pools.forEach((p, idx) => {
            const tr = document.createElement("tr");
            let nameCell = "", statusCell = "";
            if (idx === 0) {
                const statusBadge = host.reachable === true ? `<span class="badge badge-online">${escapeHtml(t("online"))}</span>`
                    : host.reachable === false ? `<span class="badge badge-offline">${escapeHtml(t("offline"))}</span>`
                    : `<span class="badge badge-stopped">${escapeHtml(t("unknown"))}</span>`;
                nameCell = `<td rowspan="${pools.length}">${escapeHtml(host.name)}</td>`;
                statusCell = `<td rowspan="${pools.length}">${statusBadge}</td>`;
            }
            const healthCls = p.health === "ONLINE" ? "badge-online" :
                (p.health ? "badge-offline" : "badge-stopped");
            const capPct = p.cap_pct != null ? p.cap_pct.toFixed(0) + "%" : "—";
            const capClass = (p.cap_pct != null && p.cap_pct >= 90) ? "color:var(--error,#f44336);font-weight:bold" :
                             (p.cap_pct != null && p.cap_pct >= 80) ? "color:#e67e22" : "";
            const free = p.free_bytes != null ? formatBytes(p.free_bytes) : "—";
            let fc;
            if (p.forecast_days_until_full == null) {
                fc = `<span class="muted">—</span>`;
            } else if (p.forecast_days_until_full < 30) {
                fc = `<span style="color:var(--error,#f44336);font-weight:bold">${p.forecast_days_until_full.toFixed(0)} ${escapeHtml(t("dash_days"))}</span>`;
            } else if (p.forecast_days_until_full < 90) {
                fc = `<span style="color:#e67e22">${p.forecast_days_until_full.toFixed(0)} ${escapeHtml(t("dash_days"))}</span>`;
            } else {
                fc = `${p.forecast_days_until_full.toFixed(0)} ${escapeHtml(t("dash_days"))}`;
            }
            tr.innerHTML = `${nameCell}${statusCell}
                <td style="font-family:monospace">${escapeHtml(p.pool)}</td>
                <td><span class="badge ${healthCls}">${escapeHtml(p.health || "?")}</span></td>
                <td style="${capClass}">${capPct}</td>
                <td>${free}</td>
                <td>${fc}</td>`;
            tb.appendChild(tr);
        });
    }
    tbl.appendChild(tb);
    hostsCard.appendChild(tbl);
    root.appendChild(hostsCard);

    // Generated-at footer
    if (d.generated_at) {
        const foot = document.createElement("p");
        foot.className = "muted";
        foot.style.cssText = "font-size:11px;text-align:right;margin-top:6px";
        foot.textContent = `${t("dash_generated_at")}: ${new Date(d.generated_at * 1000).toLocaleString()}`;
        root.appendChild(foot);
    }
    return root;
}

function copyKey(key) {
    if (!key) return;
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(key).then(() => toast(t("key_copied"), "success"));
    } else {
        const ta = document.createElement("textarea");
        ta.value = key;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand("copy");
            toast(t("key_copied"), "success");
        } catch (e) {
            const el = document.getElementById("ssh-key-display");
            if (el) {
                const range = document.createRange();
                range.selectNodeContents(el);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                toast(t("key_selected"), "info");
            }
        }
        document.body.removeChild(ta);
    }
}

async function rotateSshKey() {
    if (!confirm(t("ssh_rotate_confirm"))) return;
    if (!confirm(t("ssh_rotate_confirm2"))) return;
    toast(t("ssh_rotate_running"), "info");
    let r;
    try {
        r = await API.post("/api/ssh-key/rotate", {});
    } catch (e) {
        toast(t("ssh_rotate_failed") + ": " + (e.message || e), "error");
        return;
    }
    // Build a results modal
    const body = document.createElement("div");
    if (r.success) {
        const ok = document.createElement("div");
        ok.style.cssText = "color:var(--success, #4caf50);font-weight:bold;margin-bottom:10px";
        ok.textContent = "✅ " + t("ssh_rotate_success");
        body.appendChild(ok);
    } else {
        const err = document.createElement("div");
        err.style.cssText = "color:var(--error, #f44336);font-weight:bold;margin-bottom:10px";
        err.textContent = "❌ " + (r.error || t("ssh_rotate_failed"));
        body.appendChild(err);
    }
    if (r.warning) {
        const w = document.createElement("div");
        w.style.cssText = "background:rgba(255,165,0,0.1);border-left:3px solid orange;padding:8px;margin-bottom:10px;font-size:13px";
        w.textContent = "⚠️ " + r.warning;
        body.appendChild(w);
    }
    if (r.note) {
        const n = document.createElement("div");
        n.style.cssText = "font-size:13px;color:var(--text-secondary);margin-bottom:10px";
        n.textContent = r.note;
        body.appendChild(n);
    }
    if (Array.isArray(r.results) && r.results.length) {
        const tbl = document.createElement("table");
        tbl.style.cssText = "width:100%;font-size:13px;border-collapse:collapse";
        tbl.innerHTML = `<thead><tr>
            <th style="text-align:left;padding:4px 8px">${escapeHtml(t("host"))}</th>
            <th style="text-align:center;padding:4px 8px">${escapeHtml(t("ssh_rotate_deploy"))}</th>
            <th style="text-align:center;padding:4px 8px">${escapeHtml(t("ssh_rotate_verify"))}</th>
            <th style="text-align:center;padding:4px 8px">${escapeHtml(t("ssh_rotate_cleanup"))}</th>
        </tr></thead>`;
        const tb = document.createElement("tbody");
        for (const res of r.results) {
            const tr = document.createElement("tr");
            const mark = (v) => v === true ? "✅" : v === false ? "❌" : "—";
            tr.innerHTML = `
                <td style="padding:4px 8px">${escapeHtml(res.name || res.host)}</td>
                <td style="text-align:center;padding:4px 8px" title="${escapeHtml(res.deploy_error || '')}">${mark(res.deploy)}</td>
                <td style="text-align:center;padding:4px 8px">${mark(res.verify)}</td>
                <td style="text-align:center;padding:4px 8px">${mark(res.cleanup)}</td>`;
            tb.appendChild(tr);
        }
        tbl.appendChild(tb);
        body.appendChild(tbl);
    }
    if (r.new_pubkey) {
        const lbl = document.createElement("div");
        lbl.style.cssText = "margin-top:12px;font-size:12px;color:var(--text-secondary)";
        lbl.textContent = t("ssh_rotate_new_key") + ":";
        body.appendChild(lbl);
        const pre = document.createElement("div");
        pre.className = "key-display";
        pre.style.cssText = "margin-top:4px;word-break:break-all;font-family:monospace;font-size:11px";
        pre.textContent = r.new_pubkey;
        body.appendChild(pre);
    }
    openModal(t("ssh_rotate_title"), body.outerHTML, null);
    // Refresh Home view to show new key
    if (r.success) {
        setTimeout(() => { if (currentView === "home") viewHome(); }, 500);
    }
}

// -- Hosts -----------------------------------------------------------------
async function viewHosts() {
    setContent(loading());
    const hosts = await API.get("/api/hosts");

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("host_management")),
        h("p", {}, t("host_subtitle")),
    ]));

    // Add host form
    const formCard = h("div", { className: "card" });
    formCard.appendChild(h("div", { className: "card-header" }, t("add_new_host")));
    const formBody = h("div", { className: "card-body" });
    formBody.innerHTML = `
        <div class="form-row">
            <div class="form-group"><label>${escapeHtml(t("name"))}</label><input class="form-control" id="host-name" placeholder="pve-node-1"></div>
            <div class="form-group"><label>${escapeHtml(t("address"))}</label><input class="form-control" id="host-addr" placeholder="192.168.1.10"></div>
            <div class="form-group"><label>${escapeHtml(t("port"))}</label><input class="form-control" id="host-port" value="22" type="number"></div>
            <div class="form-group"><label>${escapeHtml(t("user"))}</label><input class="form-control" id="host-user" value="root"></div>
        </div>
        <button class="btn btn-primary" id="add-host-btn" style="margin-top:8px">${escapeHtml(t("add_host"))}</button>
    `;
    formCard.appendChild(formBody);
    container.appendChild(formCard);

    // Hosts table
    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, `${t("hosts")} (${hosts.length})`));
    if (hosts.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, [
            h("div", { className: "icon" }, "\u{1F5A5}"),
            h("p", {}, t("no_hosts")),
        ]));
    } else {
        const table = h("table");
        table.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, t("name")), h("th", {}, t("address")), h("th", {}, t("port")),
            h("th", {}, t("user")), h("th", {}, t("status")), h("th", {}, t("actions")),
        ])));
        const tbody = h("tbody");
        for (const host of hosts) {
            const tr = h("tr");
            tr.appendChild(h("td", {}, host.name));
            tr.appendChild(h("td", {}, host.address));
            tr.appendChild(h("td", {}, String(host.port)));
            tr.appendChild(h("td", {}, host.user));
            const statusTd = h("td");
            statusTd.appendChild(h("span", { className: "badge badge-stopped", id: `status-${host.address}` }, t("unknown")));
            tr.appendChild(statusTd);
            const actionsTd = h("td");
            const btnGroup = h("div", { className: "btn-group" });
            btnGroup.appendChild(h("button", {
                className: "btn btn-sm btn-success",
                onClick: () => testHost(host.address),
            }, t("test")));
            btnGroup.appendChild(h("button", {
                className: "btn btn-sm btn-danger",
                onClick: () => deleteHost(host.address),
            }, t("remove")));
            actionsTd.appendChild(btnGroup);
            tr.appendChild(actionsTd);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        tableCard.appendChild(table);
    }
    container.appendChild(tableCard);
    setContent(container);

    document.getElementById("add-host-btn").addEventListener("click", addHost);

    // Auto-probe each host in parallel so the "unknown" badge resolves
    // without requiring a manual click. Uses the same /api/hosts/test
    // endpoint as the Test button.
    for (const host of hosts) {
        _probeHostBadge(host.address);
    }
}

async function _probeHostBadge(addr) {
    const el = document.getElementById(`status-${addr}`);
    if (!el) return;
    el.textContent = t("testing");
    el.className = "badge badge-stopped";
    try {
        const r = await API.post("/api/hosts/test", { address: addr });
        el.textContent = r.success ? t("online") : t("offline");
        el.className = r.success ? "badge badge-online" : "badge badge-offline";
    } catch (e) {
        el.textContent = t("offline");
        el.className = "badge badge-offline";
    }
}

async function addHost() {
    const name = document.getElementById("host-name").value.trim();
    const addr = document.getElementById("host-addr").value.trim();
    const port = document.getElementById("host-port").value.trim();
    const user = document.getElementById("host-user").value.trim();
    if (!name || !addr) { toast(t("name_addr_required"), "error"); return; }
    const r = await API.post("/api/hosts", { name, address: addr, port, user });
    toast(r.message, r.success ? "success" : "error");
    if (r.success) {
        loadHostSelector();
        viewHosts();
    }
}

async function testHost(addr) {
    const el = document.getElementById(`status-${addr}`);
    if (el) { el.textContent = t("testing"); el.className = "badge badge-stopped"; }
    const r = await API.post("/api/hosts/test", { address: addr });
    if (el) {
        el.textContent = r.success ? t("online") : t("offline");
        el.className = `badge ${r.success ? "badge-online" : "badge-offline"}`;
    }
    toast(r.message, r.success ? "success" : "error");
}

async function deleteHost(addr) {
    if (!confirm(t("remove_host_confirm", addr))) return;
    const r = await API.del("/api/hosts", { address: addr });
    toast(r.message, r.success ? "success" : "error");
    loadHostSelector();
    viewHosts();
}

// -- Pools -----------------------------------------------------------------
async function viewPools() {
    if (!requireHost()) return;
    setContent(loading());
    const pools = await API.get(`/api/pools?host=${currentHost}`);

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("zfs_pools")),
        h("p", {}, t("pools_on", currentHost)),
    ]));

    if (pools.length === 0) {
        container.appendChild(h("div", { className: "card" }, h("div", { className: "empty-state" }, t("no_pools"))));
    }

    // Pool overview
    const statsGrid = h("div", { className: "grid grid-4" });
    for (const pool of pools) {
        const card = h("div", { className: "stat-card", style: "cursor:pointer", onClick: () => showPoolDetail(pool.name) });
        card.appendChild(h("div", { className: "stat-label" }, pool.name));
        card.appendChild(h("div", { style: "display:flex;align-items:center;gap:8px;margin-top:6px" }, [
            healthBadge(pool.health),
            h("span", { className: "badge badge-stopped", id: `upgrade-badge-${pool.name}`, style: "display:none" }, t("upgrade")),
        ]));
        card.appendChild(h("div", { style: "margin-top:8px;font-size:13px;color:var(--text-secondary)" },
            `${pool.alloc} / ${pool.size} used (${pool.cap})`));
        card.appendChild(h("div", { style: "margin-top:4px;font-size:12px;color:var(--text-secondary)" },
            `Frag: ${pool.frag} | Dedup: ${pool.dedup}`));
        statsGrid.appendChild(card);
    }
    container.appendChild(statsGrid);

    // Pool table
    const tableCard = h("div", { className: "card", style: "margin-top:16px" });
    tableCard.appendChild(h("div", { className: "card-header" }, t("all_pools")));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, t("name")), h("th", {}, t("size")), h("th", {}, t("alloc")),
        h("th", {}, t("free")), h("th", {}, t("frag")), h("th", {}, t("cap")),
        h("th", {}, t("health")), h("th", {}, t("actions")),
    ])));
    const tbody = h("tbody");
    for (const pool of pools) {
        const tr = h("tr");
        tr.appendChild(h("td", {}, h("strong", {}, pool.name)));
        tr.appendChild(h("td", {}, pool.size));
        tr.appendChild(h("td", {}, pool.alloc));
        tr.appendChild(h("td", {}, pool.free));
        tr.appendChild(h("td", {}, pool.frag));
        tr.appendChild(h("td", {}, pool.cap));
        const htd = h("td"); htd.appendChild(healthBadge(pool.health)); tr.appendChild(htd);
        const actTd = h("td");
        const bg = h("div", { className: "btn-group" });
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showPoolDetail(pool.name) }, t("details")));
        bg.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => scrubPool(pool.name) }, t("scrub")));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showPoolHistory(pool.name) }, t("history")));
        const upgradeBtn = h("button", {
            className: "btn btn-sm",
            id: `upgrade-btn-${pool.name}`,
            onClick: () => upgradePool(pool.name),
            disabled: "true",
        }, t("upgrade"));
        upgradeBtn.style.opacity = "0.4";
        bg.appendChild(upgradeBtn);
        actTd.appendChild(bg);
        tr.appendChild(actTd);
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableCard.appendChild(table);
    container.appendChild(tableCard);

    setContent(container);

    for (const pool of pools) {
        checkPoolUpgrade(pool.name);
    }
}

async function showPoolDetail(pool) {
    const [status, iostat] = await Promise.all([
        API.get(`/api/pools/status?host=${currentHost}&pool=${pool}`),
        API.get(`/api/pools/iostat?host=${currentHost}&pool=${pool}`),
    ]);
    openModal(`Pool: ${pool}`, `
        <h4 style="margin-bottom:8px">${escapeHtml(t("pool_status"))}</h4>
        <pre class="output">${escapeHtml(status.stdout || status.stderr || t("no_data"))}</pre>
        <h4 style="margin:16px 0 8px">${escapeHtml(t("io_stats"))}</h4>
        <pre class="output">${escapeHtml(iostat.stdout || iostat.stderr || t("no_data"))}</pre>
    `);
}

async function scrubPool(pool) {
    if (!confirm(t("scrub_confirm", pool))) return;
    const r = await API.post("/api/pools/scrub", { host: currentHost, pool });
    toast(r.success ? t("scrub_started") : (r.stderr || t("scrub_failed")), r.success ? "success" : "error");
}

async function showPoolHistory(pool) {
    const r = await API.get(`/api/pools/history?host=${currentHost}&pool=${pool}`);
    openModal(`${t("history")}: ${pool}`, `<pre class="output">${escapeHtml(r.stdout || r.stderr || t("no_data"))}</pre>`);
}

async function checkPoolUpgrade(pool) {
    const r = await API.get(`/api/pools/check-upgrade?host=${currentHost}&pool=${pool}`);
    const btn = document.getElementById(`upgrade-btn-${pool}`);
    const badge = document.getElementById(`upgrade-badge-${pool}`);
    if (r.upgradable) {
        if (btn) {
            btn.className = "btn btn-sm btn-success";
            btn.removeAttribute("disabled");
            btn.style.opacity = "1";
            btn.title = t("upgrade_available");
        }
        if (badge) {
            badge.className = "badge badge-online";
            badge.style.display = "inline-block";
        }
    } else {
        if (btn) {
            btn.className = "btn btn-sm";
            btn.style.opacity = "0.4";
            btn.title = t("up_to_date");
        }
    }
}

async function upgradePool(pool) {
    if (!confirm(t("upgrade_confirm", pool))) return;
    const r = await API.post("/api/pools/upgrade", { host: currentHost, pool });
    if (r.success) {
        toast(t("pool_upgraded", pool), "success");
        openModal(`${t("upgrade")}: ${pool}`, `<pre class="output">${escapeHtml(r.stdout || t("rollback_completed"))}</pre>`);
    } else {
        toast(r.stderr || t("upgrade_failed"), "error");
        openModal(`${t("upgrade_failed")}: ${pool}`, `<pre class="output">${escapeHtml(r.stderr || r.stdout || t("error"))}</pre>`);
    }
    viewPools();
}

// -- Datasets --------------------------------------------------------------
async function viewDatasets() {
    if (!requireHost()) return;
    setContent(loading());
    const datasets = await API.get(`/api/datasets?host=${currentHost}`);

    const filesystems = datasets.filter(ds => ds.type === "filesystem");
    const volumes = datasets.filter(ds => ds.type === "volume");

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("zfs_datasets")),
        h("p", {}, t("datasets_on", currentHost)),
    ]));

    const headerActions = h("div", { style: "margin-bottom:16px" });
    headerActions.appendChild(h("button", { className: "btn btn-primary", onClick: showCreateDatasetForm }, t("create_dataset")));
    container.appendChild(headerActions);

    // --- Filesystems Section ---
    const fsCard = h("div", { className: "card" });
    fsCard.appendChild(h("div", { className: "card-header" }, `📁 ${t("filesystems") || "Filesystems"} (${filesystems.length})`));
    if (filesystems.length > 0) {
        const fsTable = h("table");
        fsTable.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, t("name")), h("th", {}, t("used")),
            h("th", {}, t("avail")), h("th", {}, t("refer")), h("th", {}, t("compress")),
            h("th", {}, t("ratio")), h("th", {}, t("mountpoint") || "Mountpoint"), h("th", {}, t("actions")),
        ])));
        const fsTbody = h("tbody");
        for (const ds of filesystems) {
            const tr = h("tr");
            tr.appendChild(h("td", {}, h("strong", {}, ds.name)));
            tr.appendChild(h("td", {}, ds.used));
            tr.appendChild(h("td", {}, ds.avail));
            tr.appendChild(h("td", {}, ds.refer));
            tr.appendChild(h("td", {}, ds.compression));
            tr.appendChild(h("td", {}, ds.compressratio));
            tr.appendChild(h("td", { style: "font-size:12px;color:var(--text-secondary)" }, ds.mountpoint || "-"));
            const actTd = h("td");
            const bg = h("div", { className: "btn-group" });
            bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showDatasetProps(ds.name) }, t("properties")));
            bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => createSnapshotForDs(ds.name) }, t("snapshot")));
            actTd.appendChild(bg);
            tr.appendChild(actTd);
            fsTbody.appendChild(tr);
        }
        fsTable.appendChild(fsTbody);
        fsCard.appendChild(fsTable);
    } else {
        fsCard.appendChild(h("div", { className: "card-body", style: "color:var(--text-secondary)" }, t("no_filesystems") || "No filesystems found"));
    }
    container.appendChild(fsCard);

    // --- VM Volumes Section ---
    const volCard = h("div", { className: "card", style: "margin-top:24px" });
    volCard.appendChild(h("div", { className: "card-header" }, `💾 ${t("vm_volumes") || "VM Volumes"} (${volumes.length})`));
    if (volumes.length > 0) {
        const volTable = h("table");
        volTable.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, t("name")), h("th", {}, t("used")),
            h("th", {}, t("avail")), h("th", {}, t("refer")), h("th", {}, t("compress")),
            h("th", {}, t("ratio")), h("th", {}, t("actions")),
        ])));
        const volTbody = h("tbody");
        for (const ds of volumes) {
            const tr = h("tr");
            tr.appendChild(h("td", {}, h("strong", {}, ds.name)));
            tr.appendChild(h("td", {}, ds.used));
            tr.appendChild(h("td", {}, ds.avail));
            tr.appendChild(h("td", {}, ds.refer));
            tr.appendChild(h("td", {}, ds.compression));
            tr.appendChild(h("td", {}, ds.compressratio));
            const actTd = h("td");
            const bg = h("div", { className: "btn-group" });
            bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showDatasetProps(ds.name) }, t("properties")));
            bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => createSnapshotForDs(ds.name) }, t("snapshot")));
            actTd.appendChild(bg);
            tr.appendChild(actTd);
            volTbody.appendChild(tr);
        }
        volTable.appendChild(volTbody);
        volCard.appendChild(volTable);
    } else {
        volCard.appendChild(h("div", { className: "card-body", style: "color:var(--text-secondary)" }, t("no_volumes") || "No volumes found"));
    }
    container.appendChild(volCard);

    setContent(container);
}

async function showDatasetProps(ds) {
    const r = await API.get(`/api/datasets/properties?host=${currentHost}&dataset=${ds}`);
    openModal(`${t("properties")}: ${ds}`, `<pre class="output">${escapeHtml(r.stdout || r.stderr || t("no_data"))}</pre>`);
}

function showCreateDatasetForm() {
    openModal(t("create_dataset"), `
        <div class="form-group"><label>${escapeHtml(t("dataset_name"))}</label><input class="form-control" id="new-ds-name" placeholder="rpool/data/new-dataset"></div>
        <div class="form-group"><label>${escapeHtml(t("compression_optional"))}</label><input class="form-control" id="new-ds-compress" placeholder="lz4"></div>
    `, async () => {
        const name = document.getElementById("new-ds-name").value.trim();
        if (!name) { toast(t("name_required"), "error"); return; }
        const compress = document.getElementById("new-ds-compress").value.trim();
        const opts = compress ? { compression: compress } : null;
        const r = await API.post("/api/datasets/create", { host: currentHost, name, options: opts });
        toast(r.success ? t("dataset_created") : (r.stderr || t("failed")), r.success ? "success" : "error");
        closeModal();
        viewDatasets();
    });
}

async function destroyDataset(name) {
    if (!confirm(`DESTROY dataset ${name}?`)) return;
    if (!confirm(`Are you REALLY sure?`)) return;
    const r = await API.post("/api/datasets/destroy", { host: currentHost, name, recursive: false });
    toast(r.success ? "Dataset destroyed" : (r.stderr || t("failed")), r.success ? "success" : "error");
    viewDatasets();
}

async function createSnapshotForDs(ds) {
    const snapName = prompt(t("snapshot_name"), `manual-${Math.floor(Date.now() / 1000)}`);
    if (!snapName) return;
    const r = await API.post("/api/snapshots/create", { host: currentHost, dataset: ds, name: snapName });
    toast(r.success ? t("snapshot_created") : (r.stderr || t("failed")), r.success ? "success" : "error");
}

// -- Snapshots -------------------------------------------------------------
let _allSnapshots = [];

async function viewSnapshots() {
    if (!requireHost()) return;
    setContent(loading());
    _allSnapshots = await API.get(`/api/snapshots?host=${currentHost}`);

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("zfs_snapshots")),
        h("p", {}, t("snapshots_on", currentHost)),
    ]));

    // Stats
    const statsGrid = h("div", { className: "grid grid-3", style: "margin-bottom:16px" });
    statsGrid.appendChild(makeStatCard(t("total_snapshots"), _allSnapshots.length, ""));
    const datasets = [...new Set(_allSnapshots.map(s => s.dataset))];
    statsGrid.appendChild(makeStatCard(t("datasets"), datasets.length, ""));
    const autoSnaps = _allSnapshots.filter(s => s.snapshot.startsWith("zfs-auto-snap") || s.snapshot.startsWith("autosnap"));
    statsGrid.appendChild(makeStatCard(t("auto_snapshots"), autoSnaps.length, ""));
    container.appendChild(statsGrid);

    // Filter + Search + View toggle
    const filterCard = h("div", { className: "card" });
    const filterBody = h("div", { className: "card-body" });
    const filterRow = h("div", { className: "form-row" });
    // Dataset filter
    const dsGroup = h("div", { className: "form-group" });
    dsGroup.appendChild(h("label", {}, t("filter_by_dataset")));
    const sel = h("select", { className: "form-control", id: "snap-filter-ds" });
    sel.appendChild(h("option", { value: "" }, t("all_datasets")));
    datasets.forEach(d => sel.appendChild(h("option", { value: d }, d)));
    sel.addEventListener("change", applySnapshotFilter);
    dsGroup.appendChild(sel);
    filterRow.appendChild(dsGroup);
    // Search
    const searchGroup = h("div", { className: "form-group" });
    searchGroup.appendChild(h("label", {}, t("search")));
    const searchInput = h("input", {
        className: "form-control",
        id: "snap-search",
        placeholder: t("search_snapshot"),
        type: "text",
    });
    searchInput.addEventListener("input", applySnapshotFilter);
    searchGroup.appendChild(searchInput);
    filterRow.appendChild(searchGroup);
    // View toggle
    const viewGroup = h("div", { className: "form-group" });
    viewGroup.appendChild(h("label", {}, t("view")));
    const viewSel = h("select", { className: "form-control", id: "snap-view-mode" });
    viewSel.appendChild(h("option", { value: "table" }, t("table")));
    viewSel.appendChild(h("option", { value: "timeline" }, t("timeline")));
    viewSel.addEventListener("change", applySnapshotFilter);
    viewGroup.appendChild(viewSel);
    filterRow.appendChild(viewGroup);
    filterBody.appendChild(filterRow);
    filterCard.appendChild(filterBody);
    container.appendChild(filterCard);

    // Timeline container
    container.appendChild(h("div", { id: "snap-timeline-container" }));
    // Table container
    container.appendChild(h("div", { className: "card", id: "snap-table-card" }));

    setContent(container);
    applySnapshotFilter();
}

function applySnapshotFilter() {
    const ds = document.getElementById("snap-filter-ds").value;
    const search = (document.getElementById("snap-search")?.value || "").toLowerCase().trim();
    const mode = document.getElementById("snap-view-mode").value;
    let filtered = ds ? _allSnapshots.filter(s => s.dataset === ds) : _allSnapshots;
    if (search) {
        filtered = filtered.filter(s =>
            s.snapshot.toLowerCase().includes(search) ||
            s.dataset.toLowerCase().includes(search) ||
            s.creation.toLowerCase().includes(search)
        );
    }

    const tlContainer = document.getElementById("snap-timeline-container");
    const tableCard = document.getElementById("snap-table-card");

    if (mode === "timeline") {
        tlContainer.style.display = "block";
        tableCard.style.display = "none";
        renderTimeline(filtered);
    } else {
        tlContainer.style.display = "none";
        tableCard.style.display = "block";
        renderSnapshotTable(filtered);
    }
}

// -- Timeline --------------------------------------------------------------
function renderTimeline(snapshots) {
    const container = document.getElementById("snap-timeline-container");
    container.innerHTML = "";

    if (snapshots.length === 0) {
        container.innerHTML = `<div class="card"><div class="empty-state">${escapeHtml(t("no_snapshots"))}</div></div>`;
        return;
    }

    const grouped = {};
    for (const snap of snapshots) {
        if (!grouped[snap.dataset]) grouped[snap.dataset] = [];
        grouped[snap.dataset].push(snap);
    }

    for (const [dataset, snaps] of Object.entries(grouped)) {
        const card = h("div", { className: "card", style: "margin-bottom:16px" });
        const isVolume = snaps[0]?.ds_type === "volume";
        const typeBadge = h("span", {
            className: `badge ${isVolume ? "badge-stopped" : "badge-online"}`,
            style: "margin-left:8px;font-size:10px",
        }, isVolume ? "zvol" : "fs");
        const headerLeft = h("span", { style: "display:flex;align-items:center;gap:6px" }, [
            document.createTextNode(dataset),
            typeBadge,
        ]);
        card.appendChild(h("div", { className: "card-header" }, [
            headerLeft,
            h("span", { style: "font-size:12px;color:var(--text-secondary)" }, t("snapshots_count", snaps.length)),
        ]));

        const body = h("div", { className: "card-body", style: "padding:16px 16px 8px" });
        const timeline = h("div", { className: "snapshot-timeline" });

        for (let i = 0; i < snaps.length; i++) {
            const snap = snaps[i];
            const isAuto = snap.snapshot.startsWith("zfs-auto-snap") || snap.snapshot.startsWith("autosnap");
            const isFirst = i === 0;

            const node = h("div", { className: `tl-node ${isFirst ? "tl-node-latest" : ""} ${isAuto ? "tl-node-auto" : "tl-node-manual"}` });
            node.appendChild(h("div", { className: `tl-dot ${isFirst ? "tl-dot-latest" : isAuto ? "tl-dot-auto" : "tl-dot-manual"}` }));

            const content = h("div", { className: "tl-content" });
            const topRow = h("div", { className: "tl-top-row" });
            topRow.appendChild(h("strong", { className: "tl-snap-name" }, snap.snapshot));
            topRow.appendChild(h("span", { className: "tl-date" }, snap.creation));
            content.appendChild(topRow);

            const metaRow = h("div", { className: "tl-meta" });
            metaRow.appendChild(h("span", {}, `${t("used")}: ${snap.used}`));
            metaRow.appendChild(h("span", {}, `${t("refer")}: ${snap.refer}`));
            if (isAuto) metaRow.appendChild(h("span", { className: "badge badge-stopped", style: "font-size:9px" }, "auto"));
            content.appendChild(metaRow);

            const actions = h("div", { className: "tl-actions" });
            actions.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => rollbackSnap(snap) }, t("rollback")));
            actions.appendChild(h("button", { className: "btn btn-sm", onClick: () => cloneSnap(snap) }, t("clone")));
            actions.appendChild(h("button", { className: "btn btn-sm", onClick: () => diffSnap(snap) }, t("diff")));
            if (!isAuto) {
                actions.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: () => destroySnap(snap) }, t("delete_btn")));
            }
            content.appendChild(actions);

            node.appendChild(content);
            timeline.appendChild(node);
        }

        body.appendChild(timeline);
        card.appendChild(body);
        container.appendChild(card);
    }
}

// -- Snapshot Table --------------------------------------------------------
function renderSnapshotTable(snapshots) {
    const tableCard = document.getElementById("snap-table-card");
    tableCard.innerHTML = "";
    tableCard.appendChild(h("div", { className: "card-header" }, `${t("snapshots_count", snapshots.length)}`));

    if (snapshots.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, t("no_snapshots")));
        return;
    }

    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, t("dataset_label")), h("th", {}, t("snapshot")), h("th", {}, t("type")),
        h("th", {}, t("used")), h("th", {}, t("refer")), h("th", {}, t("created")), h("th", {}, t("actions")),
    ])));
    const tbody = h("tbody");
    for (const snap of snapshots) {
        const isVolume = snap.ds_type === "volume";
        const tr = h("tr");
        tr.appendChild(h("td", { style: "font-size:12px" }, snap.dataset));
        tr.appendChild(h("td", {}, h("strong", {}, snap.snapshot)));
        const typeTd = h("td");
        typeTd.appendChild(h("span", {
            className: `badge ${isVolume ? "badge-stopped" : "badge-online"}`,
        }, isVolume ? "zvol" : "filesystem"));
        tr.appendChild(typeTd);
        tr.appendChild(h("td", {}, snap.used));
        tr.appendChild(h("td", {}, snap.refer));
        tr.appendChild(h("td", { style: "font-size:12px" }, snap.creation));
        const actTd = h("td");
        const bg = h("div", { className: "btn-group" });
        bg.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => rollbackSnap(snap) }, t("rollback")));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => cloneSnap(snap) }, t("clone")));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => diffSnap(snap) }, t("diff")));
        const isAutoSnap = snap.snapshot.startsWith("zfs-auto-snap") || snap.snapshot.startsWith("autosnap");
        if (!isAutoSnap) {
            bg.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: () => destroySnap(snap) }, t("delete_btn")));
        }
        actTd.appendChild(bg);
        tr.appendChild(actTd);
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableCard.appendChild(table);
}

// -- Snapshot Actions ------------------------------------------------------
function _detectGuest(snap) {
    const m = snap.dataset.match(/\/(vm|subvol)-(\d+)-disk-/);
    if (m) return { vmid: m[2], vm_type: m[1] === "subvol" ? "lxc" : "qemu" };
    return null;
}

async function rollbackSnap(snap) {
    const guest = _detectGuest(snap);
    let msg = t("rollback_confirm", snap.full_name);
    if (guest) {
        const guestType = guest.vm_type === "qemu" ? "VM" : "LXC";
        msg += t("rollback_guest_hint", guestType, guest.vmid);
    }
    if (!confirm(msg)) return;
    const destroyRecent = confirm(t("destroy_recent_confirm"));

    const payload = {
        host: currentHost, snapshot: snap.full_name,
        force: true, destroy_recent: destroyRecent,
    };
    if (guest) {
        payload.stop_guest = true;
        payload.vmid = guest.vmid;
        payload.vm_type = guest.vm_type;
    }

    toast(t("performing_rollback"), "info");
    const r = await API.post("/api/snapshots/rollback", payload);
    if (r.success) {
        let resultMsg = t("rollback_completed");
        if (r.guest_actions?.stopped) resultMsg += t("guest_stopped");
        if (r.guest_actions?.started) resultMsg += t("guest_restarted");
        else if (r.guest_actions?.stopped) resultMsg += t("guest_restart_failed");
        toast(resultMsg, "success");
    } else {
        toast(r.stderr || t("rollback_failed"), "error");
    }
    viewSnapshots();
}

async function cloneSnap(snap) {
    const defaultCloneName = snap.dataset.split("/").pop() + "_" + snap.snapshot + "_CLONE";
    openModal(t("clone_snapshot"), `<div class="loading-placeholder"><span class="spinner"></span> ${t("loading_targets")}</div>`);
    const targets = await API.get(`/api/snapshots/clone-targets?host=${currentHost}`);
    const body = document.getElementById("modal-body");
    const footer = document.getElementById("modal-footer");
    if (!body) return;

    const pools = targets.pools || [];
    const datasets = targets.datasets || [];
    const snapPool = snap.full_name.split("/")[0];

    let optionsHtml = pools.map(p => `<option value="${escapeHtml(p)}"${p === snapPool ? " selected" : ""}>${escapeHtml(p)}</option>`).join("");
    datasets.forEach(ds => {
        optionsHtml += `<option value="${escapeHtml(ds)}">${escapeHtml(ds)}</option>`;
    });

    body.innerHTML = `
        <div class="form-group">
            <label>${escapeHtml(t("source_snapshot"))}</label>
            <div style="font-family:monospace;font-size:13px;padding:8px 12px;background:var(--bg-primary);border:1px solid var(--border);border-radius:6px;">${escapeHtml(snap.full_name)}</div>
        </div>
        <div class="form-group">
            <label>${escapeHtml(t("target_datastore"))}</label>
            <select class="form-control" id="clone-target-ds">${optionsHtml}</select>
        </div>
        <div class="form-group">
            <label>${escapeHtml(t("clone_name"))}</label>
            <input class="form-control" id="clone-name" value="${escapeHtml(defaultCloneName)}">
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">${escapeHtml(t("full_path"))} <span id="clone-full-path">${escapeHtml(snapPool + "/" + defaultCloneName)}</span></div>
        </div>
    `;

    const updatePath = () => {
        const target = document.getElementById("clone-target-ds").value;
        const name = document.getElementById("clone-name").value;
        const pathEl = document.getElementById("clone-full-path");
        if (pathEl) pathEl.textContent = target + "/" + name;
    };
    document.getElementById("clone-target-ds").addEventListener("change", updatePath);
    document.getElementById("clone-name").addEventListener("input", updatePath);

    footer.innerHTML = "";
    const cancelBtn = h("button", { className: "btn", onClick: () => closeModal() }, t("cancel"));
    const cloneBtn = h("button", { className: "btn btn-primary", onClick: async () => {
        const target = document.getElementById("clone-target-ds").value;
        const name = document.getElementById("clone-name").value.trim();
        if (!name) { toast(t("clone_name_required"), "error"); return; }
        const fullClone = target + "/" + name;
        cloneBtn.disabled = true;
        cloneBtn.textContent = t("cloning");
        const r = await API.post("/api/snapshots/clone", { host: currentHost, snapshot: snap.full_name, clone_name: fullClone });
        if (r.success) {
            toast(t("clone_created", fullClone), "success");
            closeModal();
            viewSnapshots();
        } else {
            toast(r.stderr || t("clone_failed"), "error");
            cloneBtn.disabled = false;
            cloneBtn.textContent = t("clone");
        }
    }}, t("clone"));
    footer.appendChild(cancelBtn);
    footer.appendChild(cloneBtn);
}

async function diffSnap(snap) {
    openModal(`${t("diff")}: ${snap.snapshot}`, `<div class="loading-placeholder"><span class="spinner"></span> ${t("loading_diff")}</div>`);
    const r = await API.get(`/api/snapshots/diff?host=${currentHost}&snapshot1=${encodeURIComponent(snap.full_name)}`);
    const body = document.getElementById("modal-body");
    if (body) {
        if (r.success) {
            body.innerHTML = `<pre class="output">${escapeHtml(r.stdout || t("no_changes"))}</pre>`;
        } else {
            body.innerHTML = `<div style="color:var(--danger);margin-bottom:12px;font-weight:600">${escapeHtml(t("diff_failed"))}</div><pre class="output">${escapeHtml(r.stderr || t("error"))}</pre>`;
        }
    }
}

async function destroySnap(snap) {
    if (!confirm(t("delete_snap_confirm", snap.full_name))) return;
    const r = await API.post("/api/snapshots/destroy", { host: currentHost, snapshot: snap.full_name });
    toast(r.success ? t("snapshot_deleted") : (r.stderr || t("delete_failed")), r.success ? "success" : "error");
    viewSnapshots();
}

// -- Snapshot Check -------------------------------------------------------
async function viewSnapshotCheck() {
    if (!requireHost()) return;
    setContent(loading());

    const data = await API.get(`/api/health/snapshot-check?host=${currentHost}`);
    const container = h("div");

    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("snapshot_check_title") || "Snapshot Check"),
        h("p", {}, `${currentHost} — ${data.datasets_analyzed || 0} Datasets`),
    ]));

    // Retention Policy Overview
    const policyCard = h("div", { className: "card" });
    policyCard.appendChild(h("div", { className: "card-header" }, t("retention_policy") || "Retention Policy (Cron)"));
    const policyBody = h("div", { className: "card-body" });
    const rp = data.retention_policy || {};
    if (Object.keys(rp).length > 0) {
        const policyGrid = h("div", { className: "grid grid-5" });
        for (const [label, keep] of Object.entries(rp)) {
            policyGrid.appendChild(makeStatCard(label, `${keep}`, "keep"));
        }
        policyBody.appendChild(policyGrid);
    } else {
        policyBody.appendChild(h("p", { style: "color:var(--text-secondary)" }, "No retention policy found in cron"));
    }
    policyCard.appendChild(policyBody);
    container.appendChild(policyCard);

    // Per-Label Status
    const labels = data.per_label || {};
    for (const [label, info] of Object.entries(labels)) {
        const hasIssues = (info.stale_datasets || []).length > 0
            || (info.gaps || []).length > 0
            || (info.count_mismatches || []).length > 0;
        const statusIcon = hasIssues ? "⚠️" : "✅";

        const card = h("div", { className: "card" });
        const header = h("div", { className: "card-header" },
            `${statusIcon} ${label} — ${info.total_snapshots} Snapshots / ${info.dataset_count} Datasets`
        );
        card.appendChild(header);
        const body = h("div", { className: "card-body" });

        // Stats row
        const statsGrid = h("div", { className: "grid grid-4" });
        statsGrid.appendChild(makeStatCard(t("per_dataset") || "Per Dataset", `${info.per_dataset_avg}`, info.configured_keep ? `keep=${info.configured_keep}` : ""));
        statsGrid.appendChild(makeStatCard(t("newest") || "Newest", info.newest_age_human || "?", ""));
        statsGrid.appendChild(makeStatCard(t("gaps_label") || "Gaps", `${(info.gaps || []).length}`, ""));
        statsGrid.appendChild(makeStatCard(t("stale_label") || "Stale", `${(info.stale_datasets || []).length}`, ""));
        body.appendChild(statsGrid);

        // Stale datasets
        if ((info.stale_datasets || []).length > 0) {
            body.appendChild(h("div", { style: "margin-top:12px;font-weight:600;color:var(--warning)" },
                `⚠️ ${t("stale_snapshots") || "Stale Snapshots"} (${info.stale_datasets.length})`));
            const tbl = _buildTable(
                [t("dataset") || "Dataset", t("age") || "Age", t("threshold") || "Threshold"],
                info.stale_datasets.filter(s => !s.note).map(s => [s.dataset, s.age, s.threshold])
            );
            body.appendChild(tbl);
        }

        // Gaps
        if ((info.gaps || []).length > 0) {
            body.appendChild(h("div", { style: "margin-top:12px;font-weight:600;color:var(--danger)" },
                `❌ ${t("gaps_found") || "Gaps Found"} (${info.gaps.length})`));
            const tbl = _buildTable(
                [t("dataset") || "Dataset", t("gap") || "Gap", t("threshold") || "Threshold"],
                info.gaps.filter(g => !g.note).map(g => [g.dataset, `${g.gap_hours}h`, `${g.threshold_hours}h`])
            );
            body.appendChild(tbl);
        }

        // Count mismatches
        if ((info.count_mismatches || []).length > 0) {
            body.appendChild(h("div", { style: "margin-top:12px;font-weight:600;color:var(--text-secondary)" },
                `ℹ️ ${t("count_mismatch") || "Count Mismatch"} (${info.count_mismatches.length})`));
            const tbl = _buildTable(
                [t("dataset") || "Dataset", t("actual") || "Actual", t("configured") || "Configured"],
                info.count_mismatches.filter(m => !m.note).map(m => [m.dataset, `${m.actual}`, `${m.configured}`])
            );
            body.appendChild(tbl);
        }

        if (!hasIssues) {
            body.appendChild(h("p", { style: "color:var(--success);margin-top:8px" }, "✅ " + (t("all_ok") || "All snapshots healthy")));
        }

        card.appendChild(body);
        container.appendChild(card);
    }

    // Missing Labels
    const missing = data.missing_labels || {};
    if (Object.keys(missing).length > 0) {
        const mCard = h("div", { className: "card" });
        mCard.appendChild(h("div", { className: "card-header" }, `⚠️ ${t("missing_labels") || "Missing Labels"}`));
        const mBody = h("div", { className: "card-body" });
        for (const [label, info] of Object.entries(missing)) {
            mBody.appendChild(h("div", { style: "margin-bottom:8px" }, [
                h("strong", {}, `${label}: `),
                h("span", {}, `${info.count} Datasets — `),
                h("code", { style: "font-size:12px" }, (info.examples || []).slice(0, 3).join(", ")),
            ]));
        }
        mCard.appendChild(mBody);
        container.appendChild(mCard);
    }

    // Manual Snapshots
    const manual = data.manual_snapshots || {};
    if (manual.total_count > 0) {
        const msCard = h("div", { className: "card" });
        msCard.appendChild(h("div", { className: "card-header" },
            `📋 ${t("manual_snapshots") || "Manual Snapshots"} (${manual.total_count} in ${manual.dataset_count} Datasets)`));
        const msBody = h("div", { className: "card-body" });
        const tbl = _buildTable(
            [t("dataset") || "Dataset", t("name") || "Name", t("age") || "Age"],
            (manual.examples || []).map(s => [s.dataset, s.name, s.age])
        );
        msBody.appendChild(tbl);
        msCard.appendChild(msBody);
        container.appendChild(msCard);
    }

    setContent(container);
}

function _buildTable(headers, rows) {
    const tbl = h("table");
    const thead = h("thead");
    const thr = h("tr");
    for (const hdr of headers) thr.appendChild(h("th", {}, hdr));
    thead.appendChild(thr);
    tbl.appendChild(thead);
    const tbody = h("tbody");
    for (const row of rows) {
        const tr = h("tr");
        for (const cell of row) {
            tr.appendChild(h("td", {}, cell || ""));
        }
        tbody.appendChild(tr);
    }
    tbl.appendChild(tbody);
    return tbl;
}

// -- Guests (VMs/CTs) -----------------------------------------------------
async function viewGuests() {
    if (!requireHost()) return;
    setContent(loading());
    const [guests, pools] = await Promise.all([
        API.get(`/api/pve/guests?host=${currentHost}`),
        API.get(`/api/pools?host=${currentHost}`),
    ]);

    const all = [...(guests.vms || []), ...(guests.cts || [])];
    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("vms_containers")),
        h("p", {}, t("guests_on", currentHost)),
    ]));

    // Stats
    const statsGrid = h("div", { className: "grid grid-3", style: "margin-bottom:16px" });
    statsGrid.appendChild(makeStatCard(t("vms"), (guests.vms || []).length, ""));
    statsGrid.appendChild(makeStatCard(t("containers"), (guests.cts || []).length, ""));
    statsGrid.appendChild(makeStatCard(t("total"), all.length, ""));
    container.appendChild(statsGrid);

    // Guest table
    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, t("all_guests")));
    if (all.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, t("no_guests")));
    } else {
        const table = h("table");
        table.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, t("vmid")), h("th", {}, t("name")), h("th", {}, t("type")),
            h("th", {}, t("status")), h("th", {}, t("actions")),
        ])));
        const tbody = h("tbody");
        for (const g of all) {
            const tr = h("tr");
            tr.appendChild(h("td", {}, h("strong", {}, g.vmid)));
            tr.appendChild(h("td", {}, g.name));
            tr.appendChild(h("td", {}, g.type === "qemu" ? "VM" : "LXC"));
            const sTd = h("td"); sTd.appendChild(statusBadge(g.status)); tr.appendChild(sTd);
            const actTd = h("td");
            const bg = h("div", { className: "btn-group" });
            bg.appendChild(h("button", {
                className: "btn btn-sm",
                onClick: () => showGuestSnapshots(g, pools),
            }, t("nav_snapshots")));
            bg.appendChild(h("button", {
                className: "btn btn-sm",
                onClick: () => createGuestSnapshot(g, pools),
            }, t("new_snapshot")));
            actTd.appendChild(bg);
            tr.appendChild(actTd);
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        tableCard.appendChild(table);
    }
    container.appendChild(tableCard);
    setContent(container);
}

async function showGuestSnapshots(guest, pools) {
    const poolName = pools.length > 0 ? pools[0].name : "rpool";
    const snaps = await API.get(`/api/pve/guest-snapshots?host=${currentHost}&pool=${poolName}&vmid=${guest.vmid}&type=${guest.type}`);

    const guestType = guest.type.toUpperCase();
    let html = `<p style="margin-bottom:12px">${escapeHtml(t("snapshots_for", guestType, guest.vmid, guest.name))}</p>`;
    if (snaps.length === 0) {
        html += `<p style="color:var(--text-secondary)">${escapeHtml(t("no_guest_snapshots"))}</p>`;
    } else {
        html += `<table><thead><tr><th>${escapeHtml(t("snapshot"))}</th><th>${escapeHtml(t("used"))}</th><th>${escapeHtml(t("refer"))}</th><th>${escapeHtml(t("created"))}</th><th>${escapeHtml(t("actions"))}</th></tr></thead><tbody>`;
        for (const s of snaps) {
            const isLxc = guest.type === "lxc";
            html += `<tr>
                <td><strong>${escapeHtml(s.snapshot)}</strong><br><span style="font-size:11px;color:var(--text-secondary)">${escapeHtml(s.dataset)}</span></td>
                <td>${s.used}</td><td>${s.refer}</td><td style="font-size:12px">${escapeHtml(s.creation)}</td>
                <td>
                    <div class="btn-group">
                        <button class="btn btn-sm btn-warning" onclick="rollbackGuestSnap('${escapeHtml(s.full_name)}')">${escapeHtml(t("rollback"))}</button>
                        ${isLxc ? `<button class="btn btn-sm btn-success" onclick="openFileRestore('${escapeHtml(s.full_name)}')">${escapeHtml(t("restore_files"))}</button>` : ''}
                        ${!isLxc ? `<button class="btn btn-sm btn-success" onclick="openVmFileRestore('${escapeHtml(s.full_name)}')">${escapeHtml(t("restore_files"))}</button>` : ''}
                    </div>
                </td>
            </tr>`;
        }
        html += '</tbody></table>';
    }
    openModal(`${t("nav_snapshots")}: ${guestType} ${guest.vmid}`, html);
}

async function createGuestSnapshot(guest, pools) {
    const poolName = pools.length > 0 ? pools[0].name : "rpool";
    const prefix = guest.type === "lxc" ? "subvol" : "vm";
    const dataset = `${poolName}/data/${prefix}-${guest.vmid}-disk-0`;
    const snapName = prompt(t("snapshot_name"), `manual-${Math.floor(Date.now() / 1000)}`);
    if (!snapName) return;
    const r = await API.post("/api/snapshots/create", { host: currentHost, dataset, name: snapName });
    toast(r.success ? t("snapshot_created") : (r.stderr || t("check_dataset_path")), r.success ? "success" : "error");
}

// Global guest snap actions
window.rollbackGuestSnap = async function(fullName) {
    const m = fullName.match(/\/(vm|subvol)-(\d+)-disk-/);
    const guest = m ? { vmid: m[2], vm_type: m[1] === "subvol" ? "lxc" : "qemu" } : null;
    let msg = t("rollback_confirm", fullName);
    if (guest) {
        const guestType = guest.vm_type === "qemu" ? "VM" : "LXC";
        msg += t("rollback_guest_hint", guestType, guest.vmid);
    }
    if (!confirm(msg)) return;
    const payload = { host: currentHost, snapshot: fullName, force: true, destroy_recent: true };
    if (guest) { payload.stop_guest = true; payload.vmid = guest.vmid; payload.vm_type = guest.vm_type; }
    toast(t("performing_rollback"), "info");
    const r = await API.post("/api/snapshots/rollback", payload);
    toast(r.success ? t("rollback_completed") : (r.stderr || t("rollback_failed")), r.success ? "success" : "error");
    closeModal();
};

window.destroyGuestSnap = async function(fullName) {
    if (!confirm(t("delete_snap_confirm", fullName))) return;
    const r = await API.post("/api/snapshots/destroy", { host: currentHost, snapshot: fullName });
    toast(r.success ? t("snapshot_deleted") : (r.stderr || t("failed")), r.success ? "success" : "error");
    closeModal();
};

// ---------------------------------------------------------------------------
// File-Level Restore (LXC)
// ---------------------------------------------------------------------------
let _restoreSession = null;

window.openFileRestore = async function(snapshotFullName) {
    closeModal();
    toast(t("mounting_snapshot"), "info");
    const r = await API.post("/api/restore/mount", { host: currentHost, snapshot: snapshotFullName });
    if (!r.success) {
        toast(r.stderr || t("mount_failed"), "error");
        return;
    }
    _restoreSession = {
        clone_ds: r.clone_ds,
        mount_path: r.mount_path,
        snapshot: snapshotFullName,
    };
    toast(t("snapshot_mounted"), "success");
    browseRestorePath("");
};

async function browseRestorePath(subpath) {
    if (!_restoreSession) return;
    const { mount_path, snapshot, clone_ds } = _restoreSession;
    const snapLabel = snapshot.includes("@") ? snapshot.split("@")[1] : snapshot;

    const pathParts = subpath ? subpath.split("/").filter(Boolean) : [];
    let breadcrumb = `<span class="restore-crumb" onclick="browseRestorePath('')">/</span>`;
    let cumulative = "";
    for (const part of pathParts) {
        cumulative += (cumulative ? "/" : "") + part;
        const p = cumulative;
        breadcrumb += ` / <span class="restore-crumb" onclick="browseRestorePath('${escapeAttr(p)}')">${escapeHtml(part)}</span>`;
    }

    const modalHtml = `
        <div style="margin-bottom:12px">
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">${escapeHtml(t("snapshot"))}: <strong>${escapeHtml(snapLabel)}</strong></div>
            <div style="font-size:13px;padding:6px 10px;background:var(--bg-primary);border:1px solid var(--border);border-radius:6px;font-family:monospace">
                ${breadcrumb}
            </div>
        </div>
        <div id="restore-file-list">
            <div class="loading-placeholder"><span class="spinner"></span> ${t("loading")}</div>
        </div>
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
            <span style="font-size:11px;color:var(--text-secondary)">${escapeHtml(t("file_restore_hint"))}</span>
            <button class="btn btn-sm btn-danger" onclick="closeFileRestore()">${escapeHtml(t("close_unmount"))}</button>
        </div>
    `;

    openModal(t("file_restore"), modalHtml);

    const r = await API.get(`/api/restore/browse?host=${currentHost}&mount_path=${encodeURIComponent(mount_path)}&path=${encodeURIComponent(subpath)}`);
    const listEl = document.getElementById("restore-file-list");
    if (!listEl) return;

    if (!r.success) {
        listEl.innerHTML = `<div style="color:var(--danger)">${escapeHtml(r.stderr || t("failed"))}</div>`;
        return;
    }

    if (r.entries.length === 0) {
        listEl.innerHTML = `<div style="color:var(--text-secondary)">${escapeHtml(t("empty_directory"))}</div>`;
        return;
    }

    const sorted = r.entries.sort((a, b) => {
        if (a.type === "dir" && b.type !== "dir") return -1;
        if (a.type !== "dir" && b.type === "dir") return 1;
        return a.name.localeCompare(b.name);
    });

    let tableHtml = `<table><thead><tr><th style="width:24px"></th><th>${escapeHtml(t("name"))}</th><th>${escapeHtml(t("size"))}</th><th>${escapeHtml(t("created"))}</th><th>${escapeHtml(t("actions"))}</th></tr></thead><tbody>`;
    if (subpath) {
        const parentPath = pathParts.slice(0, -1).join("/");
        tableHtml += `<tr style="cursor:pointer" onclick="browseRestorePath('${escapeAttr(parentPath)}')">
            <td style="font-size:16px">&#x1F519;</td><td colspan="3" style="color:var(--accent)">..</td><td></td></tr>`;
    }

    for (const entry of sorted) {
        const entryPath = subpath ? `${subpath}/${entry.name}` : entry.name;
        const icon = entry.type === "dir" ? "&#x1F4C1;" : (entry.type === "link" ? "&#x1F517;" : "&#x1F4C4;");
        const nameStyle = entry.type === "dir" ? "color:var(--accent);cursor:pointer" : "";
        const nameClick = entry.type === "dir" ? `onclick="browseRestorePath('${escapeAttr(entryPath)}')"` : "";
        const sizeDisplay = entry.type === "dir" ? "-" : entry.size;

        let actions = "";
        if (entry.type === "file") {
            actions += `<button class="btn btn-sm" onclick="previewRestoreFile('${escapeAttr(entryPath)}')">${escapeHtml(t("preview"))}</button> `;
            actions += `<button class="btn btn-sm btn-success" onclick="restoreFile('${escapeAttr(entryPath)}')">${escapeHtml(t("restore"))}</button>`;
        } else if (entry.type === "dir") {
            actions += `<button class="btn btn-sm btn-success" onclick="restoreDir('${escapeAttr(entryPath)}')">${escapeHtml(t("restore_dir"))}</button>`;
        }

        tableHtml += `<tr>
            <td style="font-size:14px">${icon}</td>
            <td style="${nameStyle}" ${nameClick}>${escapeHtml(entry.name)}</td>
            <td style="font-size:12px;color:var(--text-secondary)">${sizeDisplay}</td>
            <td style="font-size:11px;color:var(--text-secondary)">${escapeHtml(entry.date)}</td>
            <td><div class="btn-group">${actions}</div></td>
        </tr>`;
    }
    tableHtml += '</tbody></table>';
    listEl.innerHTML = tableHtml;
}

window.previewRestoreFile = async function(filePath) {
    if (!_restoreSession) return;
    const r = await API.get(`/api/restore/preview?host=${currentHost}&mount_path=${encodeURIComponent(_restoreSession.mount_path)}&file=${encodeURIComponent(filePath)}`);
    const fileName = filePath.split("/").pop();
    const previewDiv = document.getElementById("restore-file-list");
    if (!previewDiv) return;
    const prevContent = previewDiv.innerHTML;
    previewDiv.innerHTML = `
        <div style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
            <strong>${escapeHtml(fileName)}</strong>
            <button class="btn btn-sm" onclick="this.parentElement.parentElement.innerHTML = window._prevRestoreContent">${escapeHtml(t("back"))}</button>
        </div>
        <pre class="output" style="max-height:400px">${escapeHtml(r.success ? r.stdout : (r.stderr || t("cannot_preview")))}</pre>
    `;
    window._prevRestoreContent = prevContent;
};

window.restoreFile = async function(filePath) {
    if (!_restoreSession) return;
    const ds = _restoreSession.snapshot.split("@")[0];
    const defaultDest = filePath.startsWith("/") ? filePath : `/${filePath}`;
    const dest = prompt(t("restore_to_prompt"), defaultDest);
    if (!dest) return;

    const mountInfo = await API.get(`/api/datasets/properties?host=${currentHost}&dataset=${ds}`);
    let liveMountpoint = "";
    if (mountInfo.stdout) {
        const m = mountInfo.stdout.match(/mountpoint\s+(\S+)\s+/);
        if (m) liveMountpoint = m[1];
    }
    const fullDest = liveMountpoint ? `${liveMountpoint}/${dest.replace(/^\//, '')}` : dest;

    if (!confirm(t("restore_file_confirm", filePath, fullDest))) return;

    toast(t("restoring_file"), "info");
    const r = await API.post("/api/restore/file", {
        host: currentHost,
        mount_path: _restoreSession.mount_path,
        file_path: filePath,
        dest_path: fullDest,
    });
    toast(r.success ? t("file_restored", fullDest) : (r.stderr || t("restore_failed")), r.success ? "success" : "error");
};

window.restoreDir = async function(dirPath) {
    if (!_restoreSession) return;
    const ds = _restoreSession.snapshot.split("@")[0];
    const defaultDest = dirPath.startsWith("/") ? dirPath : `/${dirPath}`;
    const dest = prompt(t("restore_dir_to_prompt"), defaultDest);
    if (!dest) return;

    const mountInfo = await API.get(`/api/datasets/properties?host=${currentHost}&dataset=${ds}`);
    let liveMountpoint = "";
    if (mountInfo.stdout) {
        const m = mountInfo.stdout.match(/mountpoint\s+(\S+)\s+/);
        if (m) liveMountpoint = m[1];
    }
    const fullDest = liveMountpoint ? `${liveMountpoint}/${dest.replace(/^\//, '')}` : dest;

    if (!confirm(t("restore_dir_confirm", dirPath, fullDest))) return;

    toast(t("restoring_directory"), "info");
    const r = await API.post("/api/restore/directory", {
        host: currentHost,
        mount_path: _restoreSession.mount_path,
        dir_path: dirPath,
        dest_path: fullDest,
    });
    toast(r.success ? t("dir_restored", fullDest) : (r.stderr || t("restore_failed")), r.success ? "success" : "error");
};

window.closeFileRestore = function() {
    closeModal();
};

// ---------------------------------------------------------------------------
// File-Level Restore (VM — Zvol via kpartx)
// ---------------------------------------------------------------------------
let _zvolRestoreSession = null;

window.openVmFileRestore = async function(snapshotFullName) {
    closeModal();
    toast(t("mounting_snapshot") || "Mounting snapshot...", "info");

    const r = await API.post("/api/restore/zvol/mount", { host: currentHost, snapshot: snapshotFullName });
    if (!r.success) {
        toast(r.error || t("mount_failed"), "error");
        return;
    }

    _zvolRestoreSession = {
        snapshot: snapshotFullName,
        zvol_dev: r.zvol_dev,
        partitions: r.partitions || [],
        mount_path: null,
    };

    // Show partition selection
    showPartitionSelect();
};

function showPartitionSelect() {
    const sess = _zvolRestoreSession;
    if (!sess) return;

    const snapLabel = sess.snapshot.includes("@") ? sess.snapshot.split("@")[1] : sess.snapshot;
    const dsName = sess.snapshot.includes("@") ? sess.snapshot.split("@")[0] : "";

    let html = `
        <div style="margin-bottom:16px">
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">${escapeHtml(t("snapshot") || "Snapshot")}: <strong>${escapeHtml(snapLabel)}</strong></div>
            <div style="font-size:11px;color:var(--text-secondary)">${escapeHtml(dsName)}</div>
        </div>
    `;

    if (sess.partitions.length === 0) {
        html += `<div style="color:var(--warning);padding:16px">${escapeHtml(t("no_partitions") || "No mountable partitions found. The volume may use LVM or an unsupported filesystem.")}</div>`;
    } else {
        html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px">`;
        for (const p of sess.partitions) {
            const isEncrypted = p.encrypted === true;
            const isMountable = p.mountable !== false;
            const fsUpper = (p.fstype || "").toUpperCase();
            const fsIcon = isEncrypted ? "🔒" : (fsUpper === "NTFS" ? "🪟" : (fsUpper === "VFAT" ? "💽" : "🐧"));
            const labelText = p.label ? ` — ${p.label}` : "";

            if (isEncrypted) {
                html += `
                    <div class="card" style="opacity:0.5;cursor:not-allowed;border-color:var(--danger)">
                        <div class="card-body" style="padding:16px;text-align:center">
                            <div style="font-size:28px;margin-bottom:8px">${fsIcon}</div>
                            <div style="font-weight:700">${escapeHtml(p.name)}</div>
                            <div style="font-size:13px;color:var(--danger);margin-top:4px">
                                ${escapeHtml(fsUpper)} · ${escapeHtml(p.size)}${escapeHtml(labelText)}
                            </div>
                            <div style="font-size:11px;color:var(--danger);margin-top:6px">${escapeHtml(t("encrypted_partition") || "Encrypted — cannot mount")}</div>
                        </div>
                    </div>
                `;
            } else if (isMountable) {
                html += `
                    <div class="card" style="cursor:pointer;transition:border-color 0.2s" onclick="mountZvolPartition('${escapeAttr(p.device)}','${escapeAttr(p.fstype)}')"
                         onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
                        <div class="card-body" style="padding:16px;text-align:center">
                            <div style="font-size:28px;margin-bottom:8px">${fsIcon}</div>
                            <div style="font-weight:700">${escapeHtml(p.name)}</div>
                            <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">
                                ${escapeHtml(fsUpper)} · ${escapeHtml(p.size)}${escapeHtml(labelText)}
                            </div>
                        </div>
                    </div>
                `;
            }
        }
        html += `</div>`;
    }

    html += `
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
            <span style="font-size:11px;color:var(--text-secondary)">${escapeHtml(t("select_partition_hint") || "Select a partition to browse files")}</span>
            <button class="btn btn-sm btn-danger" onclick="closeVmRestore()">${escapeHtml(t("close_unmount") || "Close & Unmount")}</button>
        </div>
    `;

    openModal(t("vm_file_restore") || "VM File Restore", html);
}

window.mountZvolPartition = async function(device, fstype) {
    toast(t("mounting_partition") || "Mounting partition...", "info");

    const r = await API.post("/api/restore/zvol/partition", { host: currentHost, device, fstype });
    if (!r.success) {
        toast(r.error || t("mount_failed"), "error");
        return;
    }

    _zvolRestoreSession.mount_path = r.mount_path;
    _zvolRestoreSession.mounted_device = device;

    // Reuse the existing LXC file browser with the zvol mount path
    _restoreSession = {
        clone_ds: null,
        mount_path: r.mount_path,
        snapshot: _zvolRestoreSession.snapshot,
        _isZvol: true,
        _zvol_dev: _zvolRestoreSession.zvol_dev,
    };

    toast(t("partition_mounted") || "Partition mounted", "success");
    browseZvolPath("");
};

async function browseZvolPath(subpath) {
    const sess = _zvolRestoreSession;
    if (!sess || !sess.mount_path) return;

    const snapLabel = sess.snapshot.includes("@") ? sess.snapshot.split("@")[1] : sess.snapshot;
    const pathParts = subpath ? subpath.split("/").filter(Boolean) : [];
    let breadcrumb = `<span class="restore-crumb" onclick="browseZvolPath('')">/</span>`;
    let cumulative = "";
    for (const part of pathParts) {
        cumulative += (cumulative ? "/" : "") + part;
        const p = cumulative;
        breadcrumb += ` / <span class="restore-crumb" onclick="browseZvolPath('${escapeAttr(p)}')">${escapeHtml(part)}</span>`;
    }

    const modalHtml = `
        <div style="margin-bottom:12px">
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">${escapeHtml(t("snapshot") || "Snapshot")}: <strong>${escapeHtml(snapLabel)}</strong></div>
            <div style="font-size:13px;padding:6px 10px;background:var(--bg-primary);border:1px solid var(--border);border-radius:6px;font-family:monospace">
                ${breadcrumb}
            </div>
        </div>
        <div id="zvol-file-list">
            <div class="loading-placeholder"><span class="spinner"></span> ${t("loading") || "Loading..."}</div>
        </div>
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
            <div>
                <button class="btn btn-sm" onclick="showPartitionSelect()" style="margin-right:8px">${escapeHtml(t("back_to_partitions") || "← Partitions")}</button>
                <span style="font-size:11px;color:var(--text-secondary)">${escapeHtml(t("vm_restore_hint") || "Browse and download files from the VM snapshot")}</span>
            </div>
            <button class="btn btn-sm btn-danger" onclick="closeVmRestore()">${escapeHtml(t("close_unmount") || "Close & Unmount")}</button>
        </div>
    `;

    openModal(t("vm_file_restore") || "VM File Restore", modalHtml);

    const r = await API.get(`/api/restore/browse?host=${currentHost}&mount_path=${encodeURIComponent(sess.mount_path)}&path=${encodeURIComponent(subpath)}`);
    const listEl = document.getElementById("zvol-file-list");
    if (!listEl) return;

    if (!r.success) {
        listEl.innerHTML = `<div style="color:var(--danger)">${escapeHtml(r.stderr || t("failed"))}</div>`;
        return;
    }

    if (r.entries.length === 0) {
        listEl.innerHTML = `<div style="color:var(--text-secondary)">${escapeHtml(t("empty_directory") || "Empty directory")}</div>`;
        return;
    }

    const sorted = r.entries.sort((a, b) => {
        if (a.type === "dir" && b.type !== "dir") return -1;
        if (a.type !== "dir" && b.type === "dir") return 1;
        return a.name.localeCompare(b.name);
    });

    let tableHtml = `<table><thead><tr><th style="width:24px"></th><th>${escapeHtml(t("name"))}</th><th>${escapeHtml(t("size"))}</th><th>${escapeHtml(t("created") || "Date")}</th><th>${escapeHtml(t("actions"))}</th></tr></thead><tbody>`;
    if (subpath) {
        const parentPath = pathParts.slice(0, -1).join("/");
        tableHtml += `<tr style="cursor:pointer" onclick="browseZvolPath('${escapeAttr(parentPath)}')">
            <td style="font-size:16px">&#x1F519;</td><td colspan="3" style="color:var(--accent)">..</td><td></td></tr>`;
    }

    for (const entry of sorted) {
        const entryPath = subpath ? `${subpath}/${entry.name}` : entry.name;
        const icon = entry.type === "dir" ? "&#x1F4C1;" : (entry.type === "link" ? "&#x1F517;" : "&#x1F4C4;");
        const nameStyle = entry.type === "dir" ? "color:var(--accent);cursor:pointer" : "";
        const nameClick = entry.type === "dir" ? `onclick="browseZvolPath('${escapeAttr(entryPath)}')"` : "";
        const sizeDisplay = entry.type === "dir" ? "-" : entry.size;

        let actions = "";
        if (entry.type === "file") {
            actions += `<button class="btn btn-sm" onclick="previewZvolFile('${escapeAttr(entryPath)}')">${escapeHtml(t("preview"))}</button> `;
            actions += `<button class="btn btn-sm btn-success" onclick="downloadZvolFile('${escapeAttr(entryPath)}')">${escapeHtml(t("download") || "Download")}</button>`;
        } else if (entry.type === "dir") {
            actions += `<button class="btn btn-sm" onclick="browseZvolPath('${escapeAttr(entryPath)}')">${escapeHtml(t("open") || "Open")}</button>`;
        }

        tableHtml += `<tr>
            <td style="font-size:14px">${icon}</td>
            <td style="${nameStyle}" ${nameClick}>${escapeHtml(entry.name)}</td>
            <td style="font-size:12px;color:var(--text-secondary)">${sizeDisplay}</td>
            <td style="font-size:11px;color:var(--text-secondary)">${escapeHtml(entry.date)}</td>
            <td><div class="btn-group">${actions}</div></td>
        </tr>`;
    }
    tableHtml += '</tbody></table>';
    listEl.innerHTML = tableHtml;
}

window.previewZvolFile = async function(filePath) {
    const sess = _zvolRestoreSession;
    if (!sess || !sess.mount_path) return;
    const r = await API.get(`/api/restore/preview?host=${currentHost}&mount_path=${encodeURIComponent(sess.mount_path)}&file=${encodeURIComponent(filePath)}`);
    const fileName = filePath.split("/").pop();
    const listEl = document.getElementById("zvol-file-list");
    if (!listEl) return;
    const prevContent = listEl.innerHTML;
    listEl.innerHTML = `
        <div style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
            <strong>${escapeHtml(fileName)}</strong>
            <button class="btn btn-sm" onclick="this.parentElement.parentElement.innerHTML = window._prevZvolContent">${escapeHtml(t("back"))}</button>
        </div>
        <pre class="output" style="max-height:400px">${escapeHtml(r.success ? r.stdout : (r.stderr || t("cannot_preview") || "Cannot preview"))}</pre>
    `;
    window._prevZvolContent = prevContent;
};

window.downloadZvolFile = async function(filePath) {
    const sess = _zvolRestoreSession;
    if (!sess || !sess.mount_path) return;
    const r = await API.get(`/api/restore/preview?host=${currentHost}&mount_path=${encodeURIComponent(sess.mount_path)}&file=${encodeURIComponent(filePath)}`);
    if (!r.success) {
        toast(r.stderr || t("failed"), "error");
        return;
    }
    // Trigger browser download
    const fileName = filePath.split("/").pop();
    const blob = new Blob([r.stdout || ""], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast(`${fileName} ${t("downloaded") || "downloaded"}`, "success");
};

window.closeVmRestore = function() {
    // closeModal() handles the actual cleanup of _zvolRestoreSession
    closeModal();
};

// -- Auto-Snapshot ---------------------------------------------------------
async function viewAutoSnapshot() {
    if (!requireHost()) return;
    setContent(loading());
    const [status, datasets] = await Promise.all([
        API.get(`/api/auto-snapshot/status?host=${currentHost}`),
        API.get(`/api/datasets?host=${currentHost}`),
    ]);

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, "ZFS Auto-Snapshot"),
        h("p", {}, `Auto-snapshot configuration on ${currentHost}`),
    ]));

    const statusCard = h("div", { className: "card" });
    statusCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("status")),
        h("span", { className: `badge ${status.installed ? "badge-online" : "badge-offline"}` },
            status.installed ? "Installed" : "Not Installed"),
    ]));
    const statusBody = h("div", { className: "card-body" });
    if (status.cron_config) {
        statusBody.appendChild(h("h4", { style: "margin-bottom:8px" }, "Cron Configuration"));
        statusBody.appendChild(h("pre", { className: "output" }, status.cron_config));
    }
    if (status.timers) {
        statusBody.appendChild(h("h4", { style: "margin:12px 0 8px" }, "Systemd Timers"));
        statusBody.appendChild(h("pre", { className: "output" }, status.timers));
    }
    if (!status.cron_config && !status.timers) {
        statusBody.appendChild(h("p", { style: "color:var(--text-secondary)" }, "No auto-snapshot schedule detected."));
    }
    statusCard.appendChild(statusBody);
    container.appendChild(statusCard);

    const dsCard = h("div", { className: "card" });
    dsCard.appendChild(h("div", { className: "card-header" }, "Dataset Auto-Snapshot Settings"));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, t("dataset_label")),
        h("th", { style: "text-align:center;width:160px" }, "Auto-Snapshot"),
        h("th", { style: "text-align:right" }, t("actions")),
    ])));
    const tbody = h("tbody", { id: "autosnap-tbody" });
    table.appendChild(tbody);
    dsCard.appendChild(table);
    container.appendChild(dsCard);
    setContent(container);

    const tbodyEl = document.getElementById("autosnap-tbody");
    for (const ds of datasets) {
        const prop = await API.get(`/api/auto-snapshot/property?host=${currentHost}&dataset=${ds.name}`);
        const val = prop.value;
        const source = prop.source || "";
        const isActive = val === "true";
        const isInactive = val === "false";
        const isInherited = source.startsWith("inherited") || source === "default";

        const tr = h("tr");
        tr.appendChild(h("td", {}, ds.name));

        const valTd = h("td", { style: "text-align:center" });
        if (isActive) {
            const wrap = h("span", { style: "display:inline-flex;align-items:center;gap:6px" });
            wrap.appendChild(h("span", { style: "color:var(--success);font-size:20px;line-height:1" }, "\u2714"));
            if (isInherited) wrap.appendChild(h("span", { style: "font-size:10px;color:var(--text-secondary)" }, "(inherited)"));
            valTd.appendChild(wrap);
        } else if (isInactive) {
            const wrap = h("span", { style: "display:inline-flex;align-items:center;gap:6px" });
            wrap.appendChild(h("span", { style: "color:var(--danger);font-size:20px;line-height:1" }, "\u2718"));
            if (isInherited) wrap.appendChild(h("span", { style: "font-size:10px;color:var(--text-secondary)" }, "(inherited)"));
            valTd.appendChild(wrap);
        } else {
            valTd.appendChild(h("span", { style: "color:var(--text-secondary);font-size:13px" }, "not set"));
        }
        tr.appendChild(valTd);

        const actTd = h("td", { style: "text-align:right" });
        const bg = h("div", { className: "btn-group", style: "justify-content:flex-end" });
        const enableBtn = h("button", { className: "btn btn-sm btn-success" }, t("enabled"));
        const disableBtn = h("button", { className: "btn btn-sm btn-danger" }, t("disabled"));

        if (isActive) {
            enableBtn.disabled = true;
            enableBtn.style.opacity = "0.35";
            enableBtn.style.cursor = "not-allowed";
            disableBtn.addEventListener("click", () => toggleAutoSnap(ds.name, false));
        } else if (isInactive) {
            disableBtn.disabled = true;
            disableBtn.style.opacity = "0.35";
            disableBtn.style.cursor = "not-allowed";
            enableBtn.addEventListener("click", () => toggleAutoSnap(ds.name, true));
        } else {
            enableBtn.addEventListener("click", () => toggleAutoSnap(ds.name, true));
            disableBtn.addEventListener("click", () => toggleAutoSnap(ds.name, false));
        }

        bg.appendChild(enableBtn);
        bg.appendChild(disableBtn);
        actTd.appendChild(bg);
        tr.appendChild(actTd);
        tbodyEl.appendChild(tr);
    }
}

async function toggleAutoSnap(ds, enabled) {
    const r = await API.post("/api/auto-snapshot/set", { host: currentHost, dataset: ds, enabled });
    toast(r.success ? `Auto-snapshot ${enabled ? "enabled" : "disabled"} for ${ds}` : (r.stderr || t("failed")),
        r.success ? "success" : "error");
    viewAutoSnapshot();
}

// -- Health ----------------------------------------------------------------
async function viewHealth() {
    if (!requireHost()) return;
    setContent(loading());

    const [arc, events] = await Promise.all([
        API.get(`/api/health/arc?host=${currentHost}`),
        API.get(`/api/health/events?host=${currentHost}`),
    ]);

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("health_monitoring")),
        h("p", {}, t("health_on", currentHost)),
    ]));

    // ARC stats
    const arcCard = h("div", { className: "card" });
    arcCard.appendChild(h("div", { className: "card-header" }, t("arc_title")));
    const arcBody = h("div", { className: "card-body" });
    if (arc.stdout) {
        const lines = arc.stdout.trim().split("\n");
        const statsGrid = h("div", { className: "grid grid-4" });
        for (const line of lines) {
            const parts = line.trim().split(/\s+/);
            if (parts.length >= 3) {
                const name = parts[0];
                const val = parts[2];
                let displayVal = val;
                if (name === "size" || name === "c_max") {
                    const bytes = parseInt(val);
                    if (!isNaN(bytes)) displayVal = formatBytes(bytes);
                }
                statsGrid.appendChild(makeStatCard(name, displayVal, ""));
            }
        }
        arcBody.appendChild(statsGrid);
    } else {
        arcBody.appendChild(h("pre", { className: "output" }, arc.stderr || t("no_arc")));
    }
    arcCard.appendChild(arcBody);
    container.appendChild(arcCard);

    // SMART status (all disks across all pools, grouped by pool)
    const smart = await API.get(`/api/health/smart?host=${currentHost}`);
    const smartCard = h("div", { className: "card" });
    smartCard.appendChild(h("div", { className: "card-header" }, "SMART Status"));
    const smartBody = h("div", { className: "card-body" });
    if (smart.pools && Object.keys(smart.pools).length > 0) {
        let first = true;
        for (const [poolName, disks] of Object.entries(smart.pools)) {
            smartBody.appendChild(h("div", { style: `font-weight:600;margin-bottom:8px;font-size:14px${first ? "" : ";margin-top:16px;padding-top:12px;border-top:1px solid var(--border)"}` }, `Pool: ${poolName}`));
            first = false;
            for (const disk of disks) {
                const ok = disk.status.toLowerCase().includes("passed");
                const failed = disk.status.toLowerCase().includes("failed");
                const badgeCls = ok ? "badge-online" : failed ? "badge-offline" : "badge-stopped";
                smartBody.appendChild(h("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:6px;padding-left:12px" }, [
                    h("code", { style: "font-size:12px" }, disk.dev),
                    h("span", { style: "font-size:11px;color:var(--text-secondary)" }, `(${disk.id})`),
                    h("span", { className: `badge ${badgeCls}` }, disk.status || t("unknown")),
                ]));
            }
        }
    } else {
        smartBody.appendChild(h("p", { style: "color:var(--text-secondary)" }, smart.stderr || t("no_smart")));
    }
    smartCard.appendChild(smartBody);
    container.appendChild(smartCard);

    // Restore clones cleanup
    const restoreClones = await API.get(`/api/restore/clones?host=${currentHost}`);
    const rcCard = h("div", { className: "card" });
    const rcHeader = h("div", { className: "card-header" });
    rcHeader.appendChild(h("span", {}, t("restore_clones_count", restoreClones.length || 0)));
    if (restoreClones.length > 0) {
        rcHeader.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: async () => {
            if (!confirm(t("cleanup_confirm", restoreClones.length))) return;
            const r = await API.post("/api/restore/cleanup", { host: currentHost });
            if (r.success) {
                toast(t("cleaned_up", r.destroyed.length), "success");
            } else {
                toast(t("errors_label", r.errors.join(", ")), "error");
            }
            viewHealth();
        }}, t("cleanup_all")));
    }
    rcCard.appendChild(rcHeader);
    const rcBody = h("div", { className: "card-body" });
    if (restoreClones.length > 0) {
        const tbl = h("table");
        const thead = h("thead");
        const thr = h("tr");
        [t("dataset_label"), t("mountpoint"), t("used"), t("created")].forEach(txt => thr.appendChild(h("th", {}, txt)));
        thead.appendChild(thr);
        tbl.appendChild(thead);
        const tbd = h("tbody");
        for (const rc of restoreClones) {
            const tr = h("tr");
            tr.appendChild(h("td", { style: "font-family:monospace;font-size:12px" }, rc.name));
            tr.appendChild(h("td", { style: "font-family:monospace;font-size:12px" }, rc.mountpoint));
            tr.appendChild(h("td", {}, rc.used));
            tr.appendChild(h("td", { style: "font-size:12px" }, rc.creation));
            tbd.appendChild(tr);
        }
        tbl.appendChild(tbd);
        rcBody.appendChild(tbl);
    } else {
        rcBody.appendChild(h("div", { className: "empty-state" }, t("no_restore_clones")));
    }
    rcCard.appendChild(rcBody);
    container.appendChild(rcCard);

    // Zvol Restore sessions (VM volumes)
    const zvolActive = await API.get(`/api/restore/zvol/active?host=${currentHost}`);
    const zCard = h("div", { className: "card" });
    const zHeader = h("div", { className: "card-header", style: "display:flex;justify-content:space-between;align-items:center" });
    const zTotal = zvolActive.total || 0;
    zHeader.appendChild(h("span", {}, `${t("zvol_restore_sessions") || "VM Zvol Restore"} (${zTotal})`));
    if (zTotal > 0) {
        zHeader.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: async () => {
            if (!confirm(t("zvol_cleanup_confirm") || "Clean up all zvol restore sessions? This will unmount all partitions and remove kpartx mappings.")) return;
            const r = await API.post("/api/restore/zvol/cleanup", { host: currentHost });
            if (r.success) {
                toast(`${t("cleaned_up_zvol") || "Cleaned up"}: ${r.total_cleaned} items`, "success");
            }
            viewHealth();
        }}, t("cleanup_all") || "Clean up all"));
    }
    zCard.appendChild(zHeader);
    const zBody = h("div", { className: "card-body" });

    if (zTotal === 0) {
        zBody.appendChild(h("div", { className: "empty-state" }, t("no_zvol_sessions") || "No active zvol restore sessions"));
    } else {
        // Mounted partitions
        if (zvolActive.mounts && zvolActive.mounts.length > 0) {
            zBody.appendChild(h("div", { style: "font-weight:600;margin-bottom:8px" }, `${t("mounted_partitions") || "Mounted Partitions"} (${zvolActive.mounts.length})`));
            const mTbl = h("table");
            const mThead = h("thead", {}, h("tr", {}, [
                h("th", {}, t("device") || "Device"),
                h("th", {}, t("mountpoint") || "Mountpoint"),
                h("th", {}, t("type") || "Type"),
                h("th", {}, t("actions") || "Actions"),
            ]));
            mTbl.appendChild(mThead);
            const mTbody = h("tbody");
            for (const m of zvolActive.mounts) {
                const tr = h("tr");
                tr.appendChild(h("td", { style: "font-family:monospace;font-size:12px" }, m.device));
                tr.appendChild(h("td", { style: "font-family:monospace;font-size:12px" }, m.mount_path));
                tr.appendChild(h("td", {}, m.fstype || "?"));
                const actTd = h("td");
                actTd.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: async () => {
                    await API.post("/api/restore/zvol/unmount", { host: currentHost, mount_path: m.mount_path, zvol_dev: "" });
                    toast(t("unmounted") || "Unmounted", "success");
                    viewHealth();
                }}, t("unmount") || "Unmount"));
                tr.appendChild(actTd);
                mTbody.appendChild(tr);
            }
            mTbl.appendChild(mTbody);
            zBody.appendChild(mTbl);
        }

        // Device mapper entries
        if (zvolActive.mappings && zvolActive.mappings.length > 0) {
            zBody.appendChild(h("div", { style: "font-weight:600;margin-bottom:8px;margin-top:16px" }, `${t("kpartx_mappings") || "kpartx Mappings"} (${zvolActive.mappings.length})`));
            const dmList = h("div", { style: "font-family:monospace;font-size:12px;color:var(--text-secondary)" });
            for (const dm of zvolActive.mappings) {
                dmList.appendChild(h("div", { style: "padding:2px 0" }, `/dev/mapper/${dm}`));
            }
            zBody.appendChild(dmList);
        }

        // snapdev=visible volumes
        if (zvolActive.snapdev_visible && zvolActive.snapdev_visible.length > 0) {
            zBody.appendChild(h("div", { style: "font-weight:600;margin-bottom:8px;margin-top:16px;color:var(--warning)" }, `${t("snapdev_visible") || "snapdev=visible"} (${zvolActive.snapdev_visible.length})`));
            const sdList = h("div", { style: "font-family:monospace;font-size:12px" });
            for (const ds of zvolActive.snapdev_visible) {
                sdList.appendChild(h("div", { style: "padding:2px 0;color:var(--warning)" }, ds));
            }
            zBody.appendChild(sdList);
        }
    }

    zCard.appendChild(zBody);
    container.appendChild(zCard);

    // Scheduled Tasks (AI reports)
    try {
        const schedResp = await API.get("/api/ai/schedules");
        const schedList = (schedResp && schedResp.schedules) || [];
        const active = schedList.filter(s => s.enabled);

        const stCard = h("div", { className: "card" });
        stCard.appendChild(h("div", { className: "card-header" }, [
            h("span", {}, t("scheduled_tasks")),
            h("span", { className: "badge badge-online" }, String(active.length)),
        ]));
        const stBody = h("div", { className: "card-body" });

        if (active.length === 0) {
            stBody.appendChild(h("p", { style: "color:var(--text-secondary);margin:0" }, t("no_scheduled_tasks")));
        } else {
            const tbl = h("table");
            const thead = h("thead");
            thead.innerHTML = `<tr>
                <th>${escapeHtml(t("sched_host"))}</th>
                <th>${escapeHtml(t("sched_interval"))}</th>
                <th>${escapeHtml(t("sched_next_run"))}</th>
                <th>${escapeHtml(t("sched_last_run"))}</th>
                <th>${escapeHtml(t("sched_status"))}</th>
            </tr>`;
            tbl.appendChild(thead);
            const tbody = h("tbody");
            const weekdays = t("ai_weekdays").split(",");
            for (const s of active) {
                let intervalText = s.interval === "weekly"
                    ? `${t("ai_schedule_weekly")} (${weekdays[s.weekday] || "?"}, ${String(s.hour).padStart(2, "0")}:00)`
                    : `${t("ai_schedule_daily")} (${String(s.hour).padStart(2, "0")}:00)`;
                const label = s.host === null || s.host === undefined
                    ? t("sched_all_hosts")
                    : s.host;
                const tr = h("tr");
                tr.innerHTML = `
                    <td><code style="font-size:12px">${escapeHtml(label)}</code></td>
                    <td>${escapeHtml(intervalText)}</td>
                    <td>${escapeHtml(s.next_run || "—")}</td>
                    <td style="color:var(--text-secondary)">${escapeHtml(s.last_run || t("sched_never"))}</td>
                    <td><span class="badge badge-online">${escapeHtml(t("enabled"))}</span></td>
                `;
                tbody.appendChild(tr);
            }
            tbl.appendChild(tbody);
            stBody.appendChild(tbl);
        }
        stCard.appendChild(stBody);
        container.appendChild(stCard);
    } catch (e) {
        // Silently ignore if schedules endpoint unavailable
    }

    // Events (am Ende)
    const evCard = h("div", { className: "card" });
    evCard.appendChild(h("div", { className: "card-header" }, t("recent_events")));
    evCard.appendChild(h("div", { className: "card-body" }, [
        h("pre", { className: "output" }, events.stdout || events.stderr || t("no_events")),
    ]));
    container.appendChild(evCard);

    setContent(container);
}

function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

// -- Metrics (historical pool trends) --------------------------------------
function _svgLineChart(points, opts = {}) {
    // points: [{x: number, y: number}, ...]  — x is epoch, y is value
    const w = opts.width || 760;
    const hgt = opts.height || 180;
    const pad = { l: 40, r: 12, t: 12, b: 26 };
    if (!points || points.length === 0) {
        return `<div class="muted" style="padding:20px;text-align:center">${t("metrics_no_data")}</div>`;
    }
    const xs = points.map(p => p.x);
    const ys = points.map(p => p.y).filter(v => v != null && !isNaN(v));
    if (ys.length === 0) {
        return `<div class="muted" style="padding:20px;text-align:center">${t("metrics_no_data")}</div>`;
    }
    const xmin = Math.min(...xs), xmax = Math.max(...xs);
    let ymin = Math.min(...ys), ymax = Math.max(...ys);
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    if (opts.yZero) ymin = 0;
    if (opts.yMax != null) ymax = Math.max(ymax, opts.yMax);
    const xRange = xmax - xmin || 1;
    const yRange = ymax - ymin || 1;
    const plotW = w - pad.l - pad.r;
    const plotH = hgt - pad.t - pad.b;
    const px = x => pad.l + ((x - xmin) / xRange) * plotW;
    const py = y => pad.t + plotH - ((y - ymin) / yRange) * plotH;
    const path = points
        .filter(p => p.y != null && !isNaN(p.y))
        .map((p, i) => (i === 0 ? "M" : "L") + px(p.x).toFixed(1) + "," + py(p.y).toFixed(1))
        .join(" ");
    // Y axis ticks (5 steps)
    let yTicks = "";
    for (let i = 0; i <= 4; i++) {
        const v = ymin + (yRange * i / 4);
        const yp = py(v);
        const label = opts.yFmt ? opts.yFmt(v) : v.toFixed(1);
        yTicks += `<line x1="${pad.l}" y1="${yp}" x2="${w - pad.r}" y2="${yp}" stroke="#eee" stroke-width="1"/>`;
        yTicks += `<text x="${pad.l - 6}" y="${yp + 3}" text-anchor="end" font-size="10" fill="#888">${label}</text>`;
    }
    // X axis ticks (start/end timestamps)
    const fmtTs = ts => {
        const d = new Date(ts * 1000);
        return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    };
    const xTicks =
        `<text x="${pad.l}" y="${hgt - 8}" font-size="10" fill="#888">${fmtTs(xmin)}</text>` +
        `<text x="${w - pad.r}" y="${hgt - 8}" font-size="10" fill="#888" text-anchor="end">${fmtTs(xmax)}</text>`;
    const color = opts.color || "#4a90e2";
    return `<svg viewBox="0 0 ${w} ${hgt}" style="width:100%;height:${hgt}px;background:#fafbfc;border-radius:4px">
        ${yTicks}
        <path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>
        ${xTicks}
    </svg>`;
}

async function viewMetrics() {
    if (!requireHost()) { setContent(`<div class="card"><h2>${t("nav_metrics")}</h2><p>${t("select_host_first")}</p></div>`); return; }
    setContent(loading());
    const hours = parseInt(sessionStorage.getItem("metrics_hours") || "24", 10);
    const [summary, poolsInfo] = await Promise.all([
        API.get(`/api/metrics/summary?host=${encodeURIComponent(currentHost)}`),
        API.get(`/api/metrics/pools?host=${encodeURIComponent(currentHost)}`),
    ]);
    const pools = poolsInfo.pools || [];

    const container = document.createElement("div");
    container.appendChild(h("h2", {}, t("nav_metrics")));

    // Summary + controls
    const top = h("div", { className: "card" });
    const lastTs = summary.newest ? new Date(summary.newest * 1000).toLocaleString() : "—";
    const oldTs = summary.oldest ? new Date(summary.oldest * 1000).toLocaleString() : "—";
    top.innerHTML = `
        <div class="row" style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
            <div><b>${t("metrics_samples")}:</b> ${summary.samples || 0}</div>
            <div><b>${t("metrics_pools")}:</b> ${summary.pools || 0}</div>
            <div><b>${t("metrics_oldest")}:</b> ${oldTs}</div>
            <div><b>${t("metrics_newest")}:</b> ${lastTs}</div>
            <div><b>${t("metrics_interval")}:</b> ${Math.round((summary.interval_seconds || 900) / 60)} min</div>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <label>${t("metrics_range")}:</label>
            <select id="m-range">
                <option value="6">6 h</option>
                <option value="24" selected>24 h</option>
                <option value="168">7 d</option>
                <option value="720">30 d</option>
                <option value="2160">90 d</option>
            </select>
            <button class="btn btn-sm" id="m-refresh">${t("refresh")}</button>
            <button class="btn btn-sm" id="m-sample-now">${t("metrics_sample_now")}</button>
        </div>
    `;
    container.appendChild(top);

    if (pools.length === 0) {
        container.appendChild(h("div", { className: "card" }, [
            h("p", {}, t("metrics_no_samples_yet")),
        ]));
        setContent(container);
        document.getElementById("m-sample-now").onclick = async () => {
            try {
                const r = await API.post("/api/metrics/sample-now", { host: currentHost });
                if (r.success) { toast(`${r.pools_sampled} pools sampled`, "success"); viewMetrics(); }
                else { toast(r.error || "Error", "error"); }
            } catch (e) { toast(e.message, "error"); }
        };
        document.getElementById("m-refresh").onclick = () => viewMetrics();
        return;
    }

    // Fetch all pool series for this range
    for (const pool of pools) {
        const series = await API.get(`/api/metrics/series?host=${encodeURIComponent(currentHost)}&pool=${encodeURIComponent(pool)}&hours=${hours}`);
        const data = (series.data || []);
        const card = h("div", { className: "card" });
        card.innerHTML = `<h3 style="margin-top:0">${pool} <span class="muted" style="font-weight:normal;font-size:0.85em">(${data.length} ${t("metrics_samples")})</span></h3>`;

        // Three charts: capacity%, fragmentation%, alloc GB
        const capPts = data.map(d => ({ x: d.timestamp, y: d.cap_pct }));
        const fragPts = data.map(d => ({ x: d.timestamp, y: d.frag_pct }));
        const allocPts = data.map(d => ({ x: d.timestamp, y: d.alloc_bytes != null ? d.alloc_bytes / (1024 ** 3) : null }));

        const grid = h("div", { className: "metric-grid", style: "display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px" });
        const chartBox = (label, svgHtml, sub) => {
            const box = document.createElement("div");
            box.innerHTML = `<div style="font-weight:600;font-size:0.9em;margin-bottom:4px">${label}</div>
                ${svgHtml}
                <div class="muted" style="font-size:0.8em;margin-top:4px">${sub || ""}</div>`;
            return box;
        };
        const latest = data[data.length - 1] || {};
        grid.appendChild(chartBox(
            t("metrics_capacity") + " (%)",
            _svgLineChart(capPts, { yZero: true, yMax: 100, color: "#4a90e2", yFmt: v => v.toFixed(0) + "%" }),
            `${t("metrics_current")}: ${latest.cap_pct != null ? latest.cap_pct.toFixed(1) + "%" : "—"}`,
        ));
        grid.appendChild(chartBox(
            t("metrics_frag") + " (%)",
            _svgLineChart(fragPts, { yZero: true, color: "#e67e22", yFmt: v => v.toFixed(0) + "%" }),
            `${t("metrics_current")}: ${latest.frag_pct != null ? latest.frag_pct.toFixed(1) + "%" : "—"}<br><span style="font-size:0.75em;opacity:0.8">${escapeHtml(t("metrics_frag_hint"))}</span>`,
        ));
        grid.appendChild(chartBox(
            t("metrics_alloc") + " (GB)",
            _svgLineChart(allocPts, { yZero: true, color: "#27ae60", yFmt: v => v.toFixed(1) }),
            `${t("metrics_current")}: ${latest.alloc_bytes != null ? formatBytes(latest.alloc_bytes) : "—"}`,
        ));
        card.appendChild(grid);
        container.appendChild(card);
    }

    setContent(container);
    const rangeSel = document.getElementById("m-range");
    rangeSel.value = String(hours);
    rangeSel.onchange = () => { sessionStorage.setItem("metrics_hours", rangeSel.value); viewMetrics(); };
    document.getElementById("m-refresh").onclick = () => viewMetrics();
    document.getElementById("m-sample-now").onclick = async () => {
        try {
            const r = await API.post("/api/metrics/sample-now", { host: currentHost });
            if (r.success) { toast(`${r.pools_sampled} pools sampled`, "success"); viewMetrics(); }
            else { toast(r.error || "Error", "error"); }
        } catch (e) { toast(e.message, "error"); }
    };
}


// -- Audit Log -------------------------------------------------------------
function _auditFmt(ts) { return new Date(ts * 1000).toLocaleString(); }

async function viewAudit() {
    setContent(loading());
    const filter = {
        action: sessionStorage.getItem("audit_action") || "",
        user: sessionStorage.getItem("audit_user") || "",
        only_failures: sessionStorage.getItem("audit_failures") === "1",
        // Host filter is opt-in (default OFF). Many entries (login, logout,
        // host.add, config.*, cache.*) are logged with host="" and would
        // otherwise be invisible when a host is selected.
        host_filter: sessionStorage.getItem("audit_host_filter") === "1",
        limit: 200,
        offset: 0,
    };
    const params = new URLSearchParams();
    if (filter.action) params.set("action", filter.action);
    if (filter.user) params.set("user", filter.user);
    if (filter.only_failures) params.set("only_failures", "1");
    if (filter.host_filter && currentHost) params.set("host", currentHost);
    params.set("limit", String(filter.limit));

    const data = await API.get("/api/audit?" + params.toString());
    const entries = data.entries || [];

    const container = document.createElement("div");
    container.appendChild(h("h2", {}, t("nav_audit")));

    const filterCard = h("div", { className: "card" });
    const actionOpts = ['<option value="">' + t("audit_all_actions") + '</option>']
        .concat((data.actions || []).map(a => `<option value="${a}"${a === filter.action ? " selected" : ""}>${a}</option>`))
        .join("");
    filterCard.innerHTML = `
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <label>${t("audit_action")}:</label>
            <select id="a-action">${actionOpts}</select>
            <label>${t("audit_user")}:</label>
            <input id="a-user" type="text" value="${filter.user.replace(/"/g, "&quot;")}" style="width:140px" placeholder="admin"/>
            <label><input id="a-failures" type="checkbox"${filter.only_failures ? " checked" : ""}/> ${t("audit_only_failures")}</label>
            ${currentHost ? `<label><input id="a-host-filter" type="checkbox"${filter.host_filter ? " checked" : ""}/> ${t("audit_current_host")}: ${currentHost}</label>` : ""}
            <button class="btn btn-sm" id="a-apply">${t("audit_apply")}</button>
            <button class="btn btn-sm" id="a-reset">${t("audit_reset")}</button>
            <span class="muted" style="margin-left:auto">${entries.length} / ${data.total} ${t("audit_entries")}</span>
        </div>
    `;
    container.appendChild(filterCard);

    const listCard = h("div", { className: "card" });
    if (entries.length === 0) {
        listCard.innerHTML = `<p>${t("audit_no_entries")}</p>`;
    } else {
        let html = `<table><thead><tr>
            <th>${t("audit_time")}</th>
            <th>${t("audit_user")}</th>
            <th>${t("audit_ip")}</th>
            <th>${t("audit_host")}</th>
            <th>${t("audit_action")}</th>
            <th>${t("audit_target")}</th>
            <th>${t("audit_status")}</th>
            <th>${t("audit_details")}</th>
        </tr></thead><tbody>`;
        for (const e of entries) {
            const statusBadgeHtml = e.success
                ? `<span class="badge badge-online">OK</span>`
                : `<span class="badge badge-offline">FAIL</span>`;
            const det = (e.details || "").length > 80
                ? (e.details.substring(0, 80) + "…")
                : (e.details || "");
            html += `<tr>
                <td>${_auditFmt(e.timestamp)}</td>
                <td>${e.user || "—"}</td>
                <td style="font-family:monospace;font-size:0.85em">${e.ip || "—"}</td>
                <td style="font-family:monospace;font-size:0.85em">${e.host || "—"}</td>
                <td><code style="font-size:0.85em">${e.action}</code></td>
                <td style="font-family:monospace;font-size:0.85em">${(e.target || "").replace(/</g, "&lt;")}</td>
                <td>${statusBadgeHtml}</td>
                <td style="font-size:0.85em;color:#666" title="${(e.details || "").replace(/"/g, "&quot;")}">${det.replace(/</g, "&lt;")}</td>
            </tr>`;
        }
        html += "</tbody></table>";
        listCard.innerHTML = html;
    }
    container.appendChild(listCard);

    setContent(container);

    document.getElementById("a-apply").onclick = () => {
        sessionStorage.setItem("audit_action", document.getElementById("a-action").value);
        sessionStorage.setItem("audit_user", document.getElementById("a-user").value);
        sessionStorage.setItem("audit_failures", document.getElementById("a-failures").checked ? "1" : "0");
        const hf = document.getElementById("a-host-filter");
        sessionStorage.setItem("audit_host_filter", (hf && hf.checked) ? "1" : "0");
        viewAudit();
    };
    document.getElementById("a-reset").onclick = () => {
        sessionStorage.removeItem("audit_action");
        sessionStorage.removeItem("audit_user");
        sessionStorage.removeItem("audit_failures");
        sessionStorage.removeItem("audit_host_filter");
        viewAudit();
    };
}


// -- Replication (bashclub-zsync) -----------------------------------------
async function viewReplication() {
    setContent(loading());
    let hosts;
    try { hosts = await API.get("/api/hosts"); }
    catch (e) { setContent(h("p", { className: "muted" }, e.message || "Failed to load hosts")); return; }

    if (!hosts || hosts.length < 2) {
        setContent(h("div", { className: "card" },
            h("div", { className: "card-body" }, t("repl_need_two_hosts"))));
        return;
    }

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("repl_title")),
        h("p", {}, t("repl_subtitle")),
    ]));

    // -- Existing pairs overview (multi-config) ---------------------------
    // Scans every known host for /etc/bashclub/*.conf so the user sees all
    // replication pairs across the fleet at a glance and can jump to one
    // with a single click — instead of guessing source/target dropdowns.
    const pairsCard = h("div", { className: "card", style: "margin-bottom:16px" });
    const pairsRefreshBtn = h("button", { className: "btn btn-sm" }, t("refresh"));
    const pairsHeader = h("div", { className: "card-header" }, [
        h("span", {}, t("repl_pairs_title")),
        pairsRefreshBtn,
    ]);
    pairsCard.appendChild(pairsHeader);
    const pairsBody = h("div", { className: "card-body" }, h("p", { className: "muted", style: "margin:0" }, t("loading")));
    pairsCard.appendChild(pairsBody);
    container.appendChild(pairsCard);

    async function refreshPairs() {
        pairsBody.innerHTML = loading();
        // Probe every host in parallel; treat failures (host offline, no
        // bashclub installed, etc.) as "no pairs" rather than aborting.
        const probes = await Promise.all(hosts.map(async (hst) => {
            try {
                const r = await API.get("/api/replication/configs?host=" + encodeURIComponent(hst.address));
                return { host: hst, configs: r.configs || [] };
            } catch (e) {
                return { host: hst, configs: [] };
            }
        }));
        const all = [];
        probes.forEach(p => p.configs.forEach(c => {
            // Hide the bashclub default template that ships with every install
            // (/etc/bashclub/zsync.conf with placeholder source "user@host"
            // and target "pool/dataset"). Only real per-source pairs are
            // interesting in this overview.
            if ((c.path || "").endsWith("/zsync.conf")) return;
            const srcLow = (c.source || "").toLowerCase();
            const tgtLow = (c.target || "").toLowerCase();
            if (srcLow === "user@host" || tgtLow === "pool/dataset") return;
            all.push({ targetHost: p.host, ...c });
        }));

        if (!all.length) {
            pairsBody.innerHTML = "";
            pairsBody.appendChild(h("p", { className: "muted", style: "margin:0" }, t("repl_pairs_empty")));
            return;
        }

        const tbl = h("table", { className: "data-table", style: "margin:0;font-size:13px;width:100%" });
        tbl.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, t("repl_pairs_source")),
            h("th", {}, t("repl_pairs_target_host")),
            h("th", {}, t("repl_pairs_target_ds")),
            h("th", {}, t("repl_pairs_path")),
            h("th", { style: "width:200px;text-align:right" }, ""),
        ])));
        const tb = h("tbody");
        all.forEach(p => {
            const sourceAddr = (p.source || "").includes("@") ? p.source.split("@")[1] : (p.source || "");
            const openBtn = h("button", { className: "btn btn-sm btn-primary", style: "margin-right:6px" }, t("repl_pairs_open"));
            const delBtn  = h("button", { className: "btn btn-sm btn-danger" }, t("repl_pairs_delete"));
            openBtn.onclick = () => {
                // Locate matching source host in our hosts list (by address) and
                // pre-select dropdowns so renderAll() loads this pair into the wizard.
                const srcHst = hosts.find(x => x.address === sourceAddr);
                if (!srcHst) {
                    toast(t("repl_pairs_source_unknown"), "error");
                    return;
                }
                sourceSel.value = srcHst.address;
                targetSel.value = p.targetHost.address;
                renderAll();
                // scroll the wizard into view
                setTimeout(() => selCard.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
            };
            delBtn.onclick = () => openDeletePairModal(p);
            tb.appendChild(h("tr", {}, [
                h("td", { style: "font-family:monospace;font-size:12px" }, p.source || "—"),
                h("td", {}, (p.targetHost.name || p.targetHost.address)),
                h("td", { style: "font-family:monospace;font-size:12px" }, p.target || "—"),
                h("td", { style: "font-family:monospace;font-size:11px;color:var(--text-secondary)" }, p.path || ""),
                h("td", { style: "text-align:right" }, [openBtn, delBtn]),
            ]));
        });
        tbl.appendChild(tb);
        pairsBody.innerHTML = "";
        pairsBody.appendChild(tbl);
    }

    function openDeletePairModal(p) {
        // Two-stage delete: ask whether to also wipe the replica target dataset.
        const targetLabel = p.target ? p.target : "—";
        const purgeCb = h("input", { type: "checkbox", id: "repl-purge-cb" });
        const body = h("div", {}, [
            h("p", {}, t("repl_pairs_delete_intro").replace("{src}", p.source || "?").replace("{tgt}", (p.targetHost.name || p.targetHost.address))),
            h("p", { style: "font-size:12px;color:var(--text-secondary)" }, t("repl_pairs_delete_what")),
            h("ul", { style: "font-size:13px;margin:6px 0 12px 18px;color:var(--text-secondary)" }, [
                h("li", {}, t("repl_pairs_delete_li_cron")),
                h("li", {}, t("repl_pairs_delete_li_config").replace("{path}", p.path || "?")),
            ]),
            h("label", { style: "display:flex;align-items:flex-start;gap:8px;padding:10px;background:rgba(220,53,69,0.08);border:1px solid var(--danger);border-radius:6px;cursor:pointer" }, [
                purgeCb,
                h("div", {}, [
                    h("div", { style: "font-weight:600;color:var(--danger)" }, t("repl_pairs_purge_label")),
                    h("div", { style: "font-size:12px;color:var(--text-secondary);margin-top:3px" },
                        t("repl_pairs_purge_hint").replace("{ds}", targetLabel)),
                ]),
            ]),
        ]);
        const confirmBtn = h("button", { className: "btn btn-danger" }, t("repl_pairs_delete"));
        const cancelBtn = h("button", { className: "btn" }, t("cancel"));
        cancelBtn.onclick = closeModal;
        confirmBtn.onclick = async () => {
            const purge = purgeCb.checked;
            const phrase = purge ? t("repl_pairs_purge_confirm").replace("{ds}", targetLabel) : t("repl_pairs_delete_confirm");
            if (!confirm(phrase)) return;
            confirmBtn.disabled = true;
            try {
                const url = "/api/replication/config?host=" + encodeURIComponent(p.targetHost.address) +
                            "&source=" + encodeURIComponent(p.source) +
                            (purge ? "&purge=1" : "");
                const r = await API.del(url, {});
                if (!r.success) {
                    toast(r.error || t("failed"), "error");
                    closeModal(); refreshPairs(); return;
                }
                // Cron + config removal already happened synchronously above.
                // The auto-snapshot purge runs as a background task — poll it
                // so the user sees progress instead of a hung UI.
                if (purge && r.purge_task_id) {
                    toast(t("repl_pairs_purge_started"), "info");
                    closeModal(); refreshPairs();
                    pollReplicationTask(r.purge_task_id, {
                        onDone: (rec) => {
                            const res = rec.result || {};
                            const n = res.snapshots_destroyed_count || 0;
                            let msg = t("repl_pairs_delete_done") + " — " +
                                      (n > 0 ? t("repl_pairs_purge_done").replace("{n}", String(n))
                                             : t("repl_pairs_purge_none"));
                            if (res.snapshots_failed_count) {
                                msg += " (" + res.snapshots_failed_count + " " + t("repl_pairs_purge_failed_n") + ")";
                            }
                            toast(msg, "success");
                        },
                        onError: (msg) => toast(msg || t("failed"), "error"),
                    });
                } else {
                    toast(t("repl_pairs_delete_done"), "success");
                    closeModal(); refreshPairs();
                }
            } catch (e) { toast(e.message, "error"); }
            finally { confirmBtn.disabled = false; }
        };
        // openModal takes HTML strings; we want DOM nodes for the checkbox
        // event-binding to survive. Open empty, then replace body+footer.
        openModal(t("repl_pairs_delete_title"), "");
        const mb = document.getElementById("modal-body");
        const mf = document.getElementById("modal-footer");
        mb.innerHTML = ""; mb.appendChild(body);
        mf.innerHTML = "";
        mf.appendChild(cancelBtn);
        mf.appendChild(confirmBtn);
    }

    pairsRefreshBtn.onclick = refreshPairs;

    // Poll a long-running replication task. Calls onTick() on every update,
    // onDone(record) when it finishes successfully, onError(msg) on failure
    // or timeout. Polling backs off from 2s → 5s after the first minute so
    // a multi-hour first-sync doesn't hammer the server.
    function pollReplicationTask(taskId, { onTick, onDone, onError, maxSeconds = 8 * 3600 } = {}) {
        const started = Date.now();
        let interval = 2000;
        let lastProgress = "";
        const tick = async () => {
            try {
                const rec = await API.get("/api/replication/task?id=" + encodeURIComponent(taskId));
                if (rec.progress && rec.progress !== lastProgress) {
                    lastProgress = rec.progress;
                    if (onTick) onTick(rec);
                }
                if (rec.status === "done") { if (onDone) onDone(rec); return; }
                if (rec.status === "error") { if (onError) onError(rec.error || "task error"); return; }
                if ((Date.now() - started) > maxSeconds * 1000) {
                    if (onError) onError("task polling timed out"); return;
                }
                if ((Date.now() - started) > 60 * 1000) interval = 5000;
                setTimeout(tick, interval);
            } catch (e) {
                if (onError) onError(e.message || "task poll failed");
            }
        };
        tick();
    }

    // -- Step 1: Source + Target host selection ----------------------------
    const selCard = h("div", { className: "card" });
    selCard.appendChild(h("div", { className: "card-header" },
        "1. " + t("repl_pair_title")));
    const selBody = h("div", { className: "card-body" });

    const mkOptions = (selectedAddr) => {
        const opts = [h("option", { value: "" }, "— " + t("repl_choose") + " —")];
        hosts.forEach(hst => {
            const opt = h("option", { value: hst.address }, (hst.name || hst.address) + " (" + hst.address + ")");
            if (selectedAddr === hst.address) opt.selected = true;
            opts.push(opt);
        });
        return opts;
    };

    const sourceSel = h("select", { className: "form-input", style: "width:100%" }, mkOptions(""));
    const targetSel = h("select", { className: "form-input", style: "width:100%" }, mkOptions(""));

    const selGrid = h("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:12px" }, [
        h("div", {}, [
            h("label", { style: "display:block;font-size:12px;color:var(--text-secondary);margin-bottom:3px" }, t("repl_source_host")),
            sourceSel,
            h("div", { style: "font-size:11px;color:var(--text-secondary);margin-top:3px" }, t("repl_source_host_hint")),
        ]),
        h("div", {}, [
            h("label", { style: "display:block;font-size:12px;color:var(--text-secondary);margin-bottom:3px" }, t("repl_target_host")),
            targetSel,
            h("div", { style: "font-size:11px;color:var(--text-secondary);margin-top:3px" }, t("repl_target_host_hint")),
        ]),
    ]);
    selBody.appendChild(selGrid);
    selCard.appendChild(selBody);
    container.appendChild(selCard);

    // Mount points for dynamic sections (rendered after host pair is chosen)
    const setupMount    = h("div");
    const datasetsMount = h("div");
    const configMount   = h("div");
    const logMount      = h("div");
    container.appendChild(setupMount);
    container.appendChild(datasetsMount);
    container.appendChild(configMount);
    container.appendChild(logMount);

    setContent(container);

    sourceSel.onchange = () => renderAll();
    targetSel.onchange = () => renderAll();

    // Initial load of the existing-pairs overview
    refreshPairs();

    async function renderAll() {
        setupMount.innerHTML = "";
        datasetsMount.innerHTML = "";
        configMount.innerHTML = "";
        logMount.innerHTML = "";

        const src = hosts.find(x => x.address === sourceSel.value);
        const tgt = hosts.find(x => x.address === targetSel.value);
        if (!src || !tgt) return;
        if (src.address === tgt.address) {
            setupMount.appendChild(h("div", { className: "card", style: "margin-top:16px" },
                h("div", { className: "card-body" }, t("repl_same_host"))));
            return;
        }

        // qs targets the (replication) target host; qsPair adds source so the
        // backend resolves the per-source config file (/etc/bashclub/<src-ip>.conf).
        const qs = "?host=" + encodeURIComponent(tgt.address);
        const qsPair = qs + "&source=" + encodeURIComponent(src.address);
        const qsSrc = "?host=" + encodeURIComponent(src.address);

        // Load status for BOTH hosts — loading() returns an HTML string, not a node
        setupMount.innerHTML = loading();
        let status, srcStatus;
        try {
            [status, srcStatus] = await Promise.all([
                API.get("/api/replication/status" + qsPair),
                API.get("/api/replication/status" + qsSrc),
            ]);
        }
        catch (e) { setupMount.innerHTML = `<p class="muted">${escapeHtml(e.message || "Failed")}</p>`; return; }
        setupMount.innerHTML = "";

        // -- Step 2: Setup (install + SSH bootstrap) --------------------
        const setupCard = h("div", { className: "card", style: "margin-top:16px" });
        setupCard.appendChild(h("div", { className: "card-header" },
            "2. " + t("repl_setup_title")));
        const setupBody = h("div", { className: "card-body" });
        setupCard.appendChild(setupBody);
        setupMount.appendChild(setupCard);

        // Status badges — show PVE + zsync status for BOTH hosts
        const mkPveBadge = (label, st) => {
            const cls = st.is_pve ? "badge-success" : "badge-danger";
            const txt = (st.is_pve ? "\u2713 " : "\u2717 ") + label + " PVE" + (st.pve_version ? " (" + (st.pve_version.split(/\s+/)[1] || "") + ")" : "");
            return h("span", { className: "badge " + cls, title: st.pve_version || "" }, txt);
        };
        const mkZsyncBadge = (label, st) => {
            const cls = st.installed ? "badge-success" : "badge-warning";
            return h("span", { className: "badge " + cls },
                (st.installed ? "\u2713 " : "") + label + " " + t("repl_setup_zsync") + ": " + (st.installed ? t("repl_installed") : t("repl_not_installed")));
        };
        const srcLabel = "[" + t("repl_source_short") + "]";
        const tgtLabel = "[" + t("repl_target_short") + "]";
        const badgeRow = h("div", { style: "display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap" });
        badgeRow.appendChild(mkPveBadge(srcLabel, srcStatus));
        badgeRow.appendChild(mkPveBadge(tgtLabel, status));
        const zsyncBadgeSrc = mkZsyncBadge(srcLabel, srcStatus);
        const zsyncBadge = mkZsyncBadge(tgtLabel, status);
        badgeRow.appendChild(zsyncBadgeSrc);
        badgeRow.appendChild(zsyncBadge);
        const sshBadgeWrap = h("span", {}); // filled after probe
        badgeRow.appendChild(sshBadgeWrap);
        setupBody.appendChild(badgeRow);

        // Warn if either host isn't PVE
        if (!srcStatus.is_pve || !status.is_pve) {
            setupBody.appendChild(h("div", { className: "alert alert-warning", style: "margin-bottom:10px;font-size:12px" },
                t("repl_pve_warning")));
        }

        setupBody.appendChild(h("p", { style: "font-size:13px;color:var(--text-secondary);margin-bottom:10px" },
            t("repl_setup_desc").replace("{source}", src.name || src.address).replace("{target}", tgt.name || tgt.address)));

        const setupBtn = h("button", { className: "btn btn-primary" }, t("repl_setup_btn"));
        const setupResult = h("div", { style: "margin-top:10px;font-size:12px" });
        setupBody.appendChild(setupBtn);
        setupBody.appendChild(setupResult);

        setupBtn.onclick = async () => {
            if (!confirm(t("repl_setup_confirm"))) return;
            setupBtn.disabled = true;
            setupResult.innerHTML = "";
            const steps = h("div");
            setupResult.appendChild(steps);
            const addStep = (label, state) => {
                steps.appendChild(h("div", { style: "margin:3px 0" }, [
                    h("span", { style: "display:inline-block;width:20px;text-align:center;color:" + (state === "ok" ? "var(--success)" : state === "fail" ? "var(--danger)" : "var(--text-secondary)") }, state === "ok" ? "\u2713" : state === "fail" ? "\u2717" : "\u22ef"),
                    h("span", {}, " " + label),
                ]));
            };

            // Step A: install on BOTH hosts (zsync needs to be on both ends)
            const installOn = async (label, hostAddr, statusRef, badge) => {
                if (statusRef.installed) return true;
                setupBtn.textContent = t("repl_installing") + " " + label;
                addStep(t("repl_setup_step_install") + " " + label, "pending");
                try {
                    const r = await API.post("/api/replication/install?host=" + encodeURIComponent(hostAddr), {});
                    steps.lastChild.remove();
                    addStep(t("repl_setup_step_install") + " " + label, r.success ? "ok" : "fail");
                    if (!r.success) {
                        openModal(t("repl_install_failed") + " — " + label, `<pre class="output">${escapeHtml((r.stderr || "") + "\n\n" + (r.stdout || ""))}</pre>`);
                        return false;
                    }
                    statusRef.installed = true;
                    badge.className = "badge badge-success";
                    badge.textContent = "\u2713 " + label + " " + t("repl_setup_zsync") + ": " + t("repl_installed");
                    return true;
                } catch (e) {
                    steps.lastChild.remove();
                    addStep(t("repl_setup_step_install") + " " + label + " — " + e.message, "fail");
                    return false;
                }
            };
            if (!(await installOn(srcLabel, src.address, srcStatus, zsyncBadgeSrc))) {
                setupBtn.disabled = false; setupBtn.textContent = t("repl_setup_btn"); return;
            }
            if (!(await installOn(tgtLabel, tgt.address, status, zsyncBadge))) {
                setupBtn.disabled = false; setupBtn.textContent = t("repl_setup_btn"); return;
            }

            // Step B: SSH bootstrap target → source
            setupBtn.textContent = t("repl_setup_step_ssh");
            addStep(t("repl_setup_step_ssh"), "pending");
            try {
                const r = await API.post("/api/replication/bootstrap-ssh", { target: tgt.address, source: src.address });
                steps.lastChild.remove();
                addStep(t("repl_ssh_step_key") + (r.key_generated ? " (" + t("repl_ssh_generated") + ")" : " (" + t("repl_ssh_existing") + ")"), r.target_pubkey ? "ok" : "fail");
                addStep(t("repl_ssh_step_kh"), r.known_hosts_updated ? "ok" : "fail");
                addStep(t("repl_ssh_step_ak"), r.authorized_keys_updated ? "ok" : "fail");
                addStep(t("repl_ssh_step_probe"), r.probe_ok ? "ok" : "fail");
                if (r.probe_output && !r.probe_ok) {
                    setupResult.appendChild(h("pre", { style: "font-size:11px;margin-top:6px;padding:6px;background:var(--bg);border-radius:4px;white-space:pre-wrap" }, r.probe_output));
                }
                sshBadgeWrap.innerHTML = "";
                sshBadgeWrap.appendChild(h("span", { className: "badge " + (r.success ? "badge-success" : "badge-warning") },
                    (r.success ? "\u2713 " : "") + "SSH: " + (r.success ? t("repl_ssh_ok") : t("repl_ssh_not_ok"))));
                if (r.success) {
                    toast(t("repl_setup_ok"), "success");
                    // re-render to unlock the following sections (status refresh)
                    renderAll();
                } else {
                    toast(r.error || t("repl_ssh_bootstrap_failed"), "error");
                }
            } catch (e) { toast(e.message, "error"); }
            finally {
                setupBtn.disabled = false;
                setupBtn.textContent = t("repl_setup_btn");
            }
        };

        // Bail here if not installed — user must click Setup first
        if (!status.installed) return;

        // -- Step 3: Datasets to replicate (from SOURCE) ------------------
        const dsCard = h("div", { className: "card", style: "margin-top:16px" });
        dsCard.appendChild(h("div", { className: "card-header" },
            "3. " + t("repl_datasets_title")));
        const dsBody = h("div", { className: "card-body" });
        dsCard.appendChild(dsBody);
        datasetsMount.appendChild(dsCard);

        dsBody.appendChild(h("p", { style: "font-size:13px;color:var(--text-secondary);margin-bottom:8px" },
            t("repl_datasets_desc").replace("{source}", src.name || src.address)));

        const tagInputRow = h("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:10px" }, [
            h("label", { style: "font-size:12px;color:var(--text-secondary)" }, t("repl_f_tag") + ":"),
            h("input", { type: "text", className: "form-input", id: "repl-tag-input", value: (status.config && status.config.tag) || "bashclub:zsync", style: "max-width:220px" }),
            h("button", { className: "btn btn-sm" }, t("refresh")),
        ]);
        const tagInput = tagInputRow.querySelector("input");
        const tagRefreshBtn = tagInputRow.querySelector("button");
        dsBody.appendChild(tagInputRow);

        const dsListWrap = h("div", { style: "max-height:320px;overflow:auto;border:1px solid var(--border);border-radius:4px;padding:8px;background:var(--bg)" }, t("loading"));
        dsBody.appendChild(dsListWrap);

        const dsActionRow = h("div", { style: "margin-top:10px;display:flex;gap:8px;flex-wrap:wrap" });
        const dsSaveBtn = h("button", { className: "btn btn-primary" }, t("repl_datasets_apply"));
        dsActionRow.appendChild(dsSaveBtn);
        dsBody.appendChild(dsActionRow);

        // Track initial state so we only send diffs on Apply
        let initialTagged = new Set();
        let currentChecks = new Map(); // name -> checkbox

        async function loadDatasets() {
            const srcQs = "?host=" + encodeURIComponent(src.address) + "&tag=" + encodeURIComponent(tagInput.value.trim() || "bashclub:zsync");
            dsListWrap.textContent = t("loading");
            try {
                const r = await API.get("/api/replication/tagged-datasets" + srcQs);
                dsListWrap.innerHTML = "";
                currentChecks.clear();
                initialTagged = new Set();
                if (!r.datasets || !r.datasets.length) {
                    dsListWrap.appendChild(h("p", { className: "muted" }, t("repl_datasets_none")));
                    return;
                }
                r.datasets.forEach(d => {
                    if (d.tagged) initialTagged.add(d.name);
                    const row = h("label", { style: "display:flex;gap:8px;padding:4px 6px;align-items:center;cursor:pointer;border-radius:3px" });
                    const cb = h("input", { type: "checkbox" });
                    if (d.tagged) cb.checked = true;
                    currentChecks.set(d.name, cb);
                    row.appendChild(cb);
                    row.appendChild(h("span", { style: "font-family:monospace;font-size:13px;flex:1" }, d.name));
                    row.appendChild(h("span", { style: "font-size:11px;color:var(--text-secondary)" }, d.type));
                    dsListWrap.appendChild(row);
                });
            } catch (e) { dsListWrap.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`; }
        }
        tagRefreshBtn.onclick = loadDatasets;
        loadDatasets();

        dsSaveBtn.onclick = async () => {
            const tag = tagInput.value.trim() || "bashclub:zsync";
            const enable = [], disable = [];
            currentChecks.forEach((cb, name) => {
                if (cb.checked && !initialTagged.has(name)) enable.push(name);
                if (!cb.checked && initialTagged.has(name)) disable.push(name);
            });
            if (!enable.length && !disable.length) { toast(t("repl_datasets_nochange"), "info"); return; }
            dsSaveBtn.disabled = true;
            try {
                const r = await API.post("/api/replication/set-tags", {
                    host: src.address, tag, enable, disable,
                });
                if (r.success) {
                    toast(t("repl_datasets_applied").replace("{n}", String(enable.length + disable.length)), "success");
                    loadDatasets();
                } else {
                    const failed = (r.results || []).filter(x => !x.success).map(x => x.dataset + ": " + (x.stderr || "?"));
                    openModal(t("repl_datasets_failed"), `<pre class="output">${escapeHtml(failed.join("\n"))}</pre>`);
                }
            } catch (e) { toast(e.message, "error"); }
            finally { dsSaveBtn.disabled = false; }
        };

        // -- Step 4: Config -----------------------------------------------
        const cfgCard = h("div", { className: "card", style: "margin-top:16px" });
        cfgCard.appendChild(h("div", { className: "card-header" },
            "4. " + t("repl_config_title")));
        const cfgBody = h("div", { className: "card-body" });
        cfgCard.appendChild(cfgBody);
        configMount.appendChild(cfgCard);

        const v = status.config || {};
        const defSourceStr = (src.user || "root") + "@" + src.address;
        const defSourcePort = String(src.port || 22);

        // Target dataset dropdown
        let datasets = [];
        try {
            const ds = await API.get("/api/datasets" + qs);
            datasets = (ds.filesystems || ds || []).map(d => d.name || d).filter(n => typeof n === "string");
        } catch (_) {}

        const tgtSelect = h("select", { className: "form-input", style: "width:100%" }, [
            h("option", { value: "" }, "— " + t("repl_choose") + " —"),
            ...datasets.map(name => {
                const opt = h("option", { value: name }, name);
                if (name === v.target) opt.selected = true;
                return opt;
            }),
            h("option", { value: "__new__" }, "+ " + t("repl_create_target")),
        ]);
        const tgtNewInput = h("input", { type: "text", className: "form-input", style: "margin-top:6px;display:none", placeholder: "rpool/repl", value: "rpool/repl" });
        const tgtCreateBtn = h("button", { className: "btn btn-sm btn-warning", style: "margin-top:6px;margin-left:6px;display:none" }, t("repl_create_target_btn"));
        tgtSelect.onchange = () => {
            const show = tgtSelect.value === "__new__";
            tgtNewInput.style.display = show ? "inline-block" : "none";
            tgtCreateBtn.style.display = show ? "inline-block" : "none";
        };
        tgtCreateBtn.onclick = async () => {
            const name = tgtNewInput.value.trim();
            if (!name) { toast(t("repl_create_target_missing"), "error"); return; }
            if (!confirm(t("repl_create_target_confirm").replace("{ds}", name))) return;
            tgtCreateBtn.disabled = true;
            try {
                const r = await API.post("/api/replication/create-target" + qs, { dataset: name });
                if (r.success) {
                    toast(r.created ? t("repl_create_target_created") : t("repl_create_target_existed"), "success");
                    const opt = h("option", { value: name }, name);
                    opt.selected = true;
                    tgtSelect.insertBefore(opt, tgtSelect.querySelector("option[value='__new__']"));
                    tgtSelect.value = name;
                    tgtNewInput.style.display = "none";
                    tgtCreateBtn.style.display = "none";
                } else {
                    toast(r.error || r.stderr || t("failed"), "error");
                }
            } catch (e) { toast(e.message, "error"); }
            finally { tgtCreateBtn.disabled = false; }
        };

        const cfgInputs = {};
        const form = h("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:12px" });

        const tgtCell = h("div");
        tgtCell.appendChild(h("label", { style: "display:block;font-size:12px;color:var(--text-secondary);margin-bottom:3px" }, t("repl_f_target")));
        tgtCell.appendChild(tgtSelect);
        tgtCell.appendChild(h("div", { style: "white-space:nowrap" }, [tgtNewInput, tgtCreateBtn]));
        tgtCell.appendChild(h("div", { style: "font-size:11px;color:var(--text-secondary);margin-top:3px" }, t("repl_h_target")));
        form.appendChild(tgtCell);
        cfgInputs.target = { get: () => tgtSelect.value === "__new__" ? tgtNewInput.value.trim() : tgtSelect.value };

        const srcCell = h("div");
        srcCell.appendChild(h("label", { style: "display:block;font-size:12px;color:var(--text-secondary);margin-bottom:3px" }, t("repl_f_source")));
        const srcInput = h("input", { type: "text", className: "form-input", value: defSourceStr, readonly: true, style: "background:var(--bg)" });
        srcCell.appendChild(srcInput);
        srcCell.appendChild(h("div", { style: "font-size:11px;color:var(--text-secondary);margin-top:3px" }, t("repl_h_source_auto")));
        form.appendChild(srcCell);
        cfgInputs.source = { get: () => srcInput.value.trim() };

        // Field list mirrors the upstream default /etc/bashclub/zsync.conf.
        // ``def`` is the value applied when the field is left empty on save —
        // the placeholder shows it, so the form looks clean but a save still
        // produces a complete, ready-to-run config.
        const simple = [
            { key: "sshport",                      label: "repl_f_sshport",                      hint: "repl_h_sshport",                      def: String(defSourcePort || "22") },
            { key: "tag",                          label: "repl_f_tag",                          hint: "repl_h_tag",                          def: "bashclub:zsync" },
            { key: "snapshot_filter",              label: "repl_f_snapshot_filter",              hint: "repl_h_snapshot_filter",              def: "hourly|daily|weekly|monthly" },
            { key: "min_keep",                     label: "repl_f_min_keep",                     hint: "repl_h_min_keep",                     def: "2" },
            { key: "zfs_auto_snapshot_keep",       label: "repl_f_zas_keep",                     hint: "repl_h_zas_keep",                     def: "0" },
            { key: "zfs_auto_snapshot_label",      label: "repl_f_zas_label",                    hint: "repl_h_zas_label",                    def: "backup" },
            { key: "zfs_auto_snapshot_engine",     label: "repl_f_engine",                       hint: "repl_h_engine",                       def: "zas" },
            { key: "prefix",                       label: "repl_f_prefix",                       hint: "",                                    def: "" },
            { key: "suffix",                       label: "repl_f_suffix",                       hint: "",                                    def: "" },
            { key: "checkzfs_disabled",            label: "repl_f_checkzfs_disabled",            hint: "repl_h_checkzfs_disabled",            def: "0" },
            { key: "checkzfs_local",               label: "repl_f_checkzfs_local",               hint: "repl_h_checkzfs_local",               def: "0" },
            { key: "checkzfs_prefix",              label: "repl_f_checkzfs_prefix",              hint: "repl_h_checkzfs_prefix",              def: "zsync" },
            { key: "checkzfs_max_age",             label: "repl_f_checkzfs_max_age",             hint: "repl_h_checkzfs_max_age",             def: "1500,6000" },
            { key: "checkzfs_max_snapshot_count",  label: "repl_f_checkzfs_max_count",           hint: "repl_h_checkzfs_max_count",           def: "150,165" },
            { key: "checkzfs_spool",               label: "repl_f_checkzfs_spool",               hint: "repl_h_checkzfs_spool",               def: "0" },
            { key: "checkzfs_spool_maxage",        label: "repl_f_checkzfs_spool_maxage",        hint: "repl_h_checkzfs_spool_maxage",        def: "87000" },
        ];
        simple.forEach(f => {
            const cell = h("div");
            cell.appendChild(h("label", { style: "display:block;font-size:12px;color:var(--text-secondary);margin-bottom:3px" }, t(f.label)));
            const input = h("input", { type: "text", className: "form-input", placeholder: f.def, value: v[f.key] ?? "" });
            // Empty input -> apply the default. This keeps the form tidy but
            // still produces a complete config on save.
            cfgInputs[f.key] = { get: () => {
                const val = input.value.trim();
                return val === "" ? f.def : val;
            } };
            cell.appendChild(input);
            if (f.hint) cell.appendChild(h("div", { style: "font-size:11px;color:var(--text-secondary);margin-top:3px" }, t(f.hint)));
            form.appendChild(cell);
        });
        cfgBody.appendChild(form);

        const cfgBtnRow = h("div", { style: "margin-top:15px;display:flex;gap:8px;flex-wrap:wrap" });
        const saveBtn = h("button", { className: "btn btn-primary" }, t("save"));
        saveBtn.onclick = async () => {
            const values = {};
            Object.keys(cfgInputs).forEach(k => { values[k] = cfgInputs[k].get(); });
            if (!values.target) { toast(t("repl_target_required"), "error"); return; }
            if (!values.source) { toast(t("repl_source_required"), "error"); return; }
            saveBtn.disabled = true;
            try {
                const r = await API.post("/api/replication/config" + qsPair, { values, source: src.address });
                if (r.success) toast(t("repl_config_saved"), "success");
                else toast(r.stderr || t("failed"), "error");
            } catch (e) { toast(e.message, "error"); }
            finally { saveBtn.disabled = false; }
        };
        cfgBtnRow.appendChild(saveBtn);

        const runBtn = h("button", { className: "btn btn-warning" }, t("repl_run_now"));
        runBtn.onclick = async () => {
            if (!confirm(t("repl_run_confirm"))) return;
            runBtn.disabled = true; runBtn.textContent = t("repl_running");
            try {
                const r = await API.post("/api/replication/run" + qsPair, { source: src.address });
                if (!r.success || !r.task_id) {
                    toast(r.error || t("repl_run_failed"), "error");
                    return;
                }
                toast(t("repl_run_started"), "info");
                // Poll the task until it finishes; refresh log along the way so
                // the user sees zsync progress even before the run is done.
                pollReplicationTask(r.task_id, {
                    onTick: () => refreshLog(),
                    onDone: (rec) => {
                        const ok = rec.result && rec.result.success;
                        const exit = rec.result ? rec.result.exit_code : "?";
                        toast(ok ? t("repl_run_ok") : t("repl_run_failed") + " (exit=" + exit + ")",
                              ok ? "success" : "error");
                        refreshLog();
                        runBtn.disabled = false; runBtn.textContent = t("repl_run_now");
                    },
                    onError: (msg) => {
                        toast(msg || t("repl_run_failed"), "error");
                        runBtn.disabled = false; runBtn.textContent = t("repl_run_now");
                    },
                });
            } catch (e) {
                toast(e.message, "error");
                runBtn.disabled = false; runBtn.textContent = t("repl_run_now");
            }
        };
        cfgBtnRow.appendChild(runBtn);
        cfgBody.appendChild(cfgBtnRow);

        // -- Step 5: Cron schedule ----------------------------------------
        const cronCard = h("div", { className: "card", style: "margin-top:16px" });
        cronCard.appendChild(h("div", { className: "card-header" }, "5. " + t("repl_cron_title")));
        const cronBody = h("div", { className: "card-body" });
        cronCard.appendChild(cronBody);
        configMount.appendChild(cronCard);

        cronBody.appendChild(h("p", { style: "font-size:12px;color:var(--text-secondary);margin-bottom:8px" },
            t("repl_cron_desc")));

        const cronStatusLine = h("div", { style: "margin-bottom:10px;font-size:13px" }, t("loading"));
        cronBody.appendChild(cronStatusLine);

        const cronPresetSel = h("select", { className: "form-input", style: "max-width:340px" });
        const cronCustomInput = h("input", { type: "text", className: "form-input", style: "max-width:240px;font-family:monospace", placeholder: "20 0-22 * * *" });
        const cronPreview = h("code", { style: "font-size:12px;color:var(--text-secondary)" }, "");

        // Presets are loaded from the backend response; fallback list mirrors
        // the bashclub default if the call fails.
        const fallbackPresets = [
            { id: "bashclub_default", label: "repl_cron_preset_default", schedule: "20 0-22 * * *" },
            { id: "every_15min", label: "repl_cron_preset_15min", schedule: "*/15 * * * *" },
            { id: "every_30min", label: "repl_cron_preset_30min", schedule: "*/30 * * * *" },
            { id: "hourly", label: "repl_cron_preset_hourly", schedule: "0 * * * *" },
            { id: "every_2h", label: "repl_cron_preset_2h", schedule: "0 */2 * * *" },
            { id: "every_6h", label: "repl_cron_preset_6h", schedule: "0 */6 * * *" },
            { id: "daily_0300", label: "repl_cron_preset_daily", schedule: "0 3 * * *" },
        ];

        const buildPresets = (presets) => {
            cronPresetSel.innerHTML = "";
            (presets || fallbackPresets).forEach(p => {
                cronPresetSel.appendChild(h("option", { value: p.schedule, "data-id": p.id }, t(p.label) + " — " + p.schedule));
            });
            cronPresetSel.appendChild(h("option", { value: "__custom__" }, t("repl_cron_custom")));
        };
        buildPresets(fallbackPresets);

        const cronRow1 = h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px" }, [
            h("label", { style: "font-size:12px;color:var(--text-secondary);min-width:90px" }, t("repl_cron_preset") + ":"),
            cronPresetSel,
        ]);
        const cronRow2 = h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px" }, [
            h("label", { style: "font-size:12px;color:var(--text-secondary);min-width:90px" }, t("repl_cron_expr") + ":"),
            cronCustomInput,
            cronPreview,
        ]);
        cronBody.appendChild(cronRow1);
        cronBody.appendChild(cronRow2);

        const updatePreview = () => {
            const expr = cronPresetSel.value === "__custom__" ? cronCustomInput.value.trim() : cronPresetSel.value;
            cronPreview.textContent = expr ? ("→ " + expr + " bashclub-zsync -c " + (status.config_path || "/etc/bashclub/" + (src.address || "").replace(/[^A-Za-z0-9._-]/g, "_") + ".conf")) : "";
        };
        cronPresetSel.onchange = () => {
            cronCustomInput.value = cronPresetSel.value === "__custom__" ? cronCustomInput.value : cronPresetSel.value;
            updatePreview();
        };
        cronCustomInput.oninput = updatePreview;

        const cronBtnRow = h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:8px" });
        const cronSaveBtn = h("button", { className: "btn btn-primary" }, t("repl_cron_apply"));
        const cronRemoveBtn = h("button", { className: "btn btn-danger" }, t("repl_cron_remove"));
        cronBtnRow.appendChild(cronSaveBtn);
        cronBtnRow.appendChild(cronRemoveBtn);
        cronBody.appendChild(cronBtnRow);

        async function refreshCron() {
            cronStatusLine.textContent = t("loading");
            try {
                const r = await API.get("/api/replication/cron" + qsPair);
                buildPresets(r.presets);
                if (r.installed && r.entry) {
                    cronStatusLine.innerHTML = "";
                    cronStatusLine.appendChild(h("span", { className: "badge badge-success", style: "margin-right:8px" }, t("repl_cron_active")));
                    cronStatusLine.appendChild(h("code", { style: "font-size:12px" }, r.entry.raw));
                    // Pre-select matching preset, else custom
                    let matched = false;
                    for (const opt of cronPresetSel.options) {
                        if (opt.value === r.entry.schedule) { opt.selected = true; matched = true; break; }
                    }
                    if (!matched) {
                        cronPresetSel.value = "__custom__";
                        cronCustomInput.value = r.entry.schedule;
                    } else {
                        cronCustomInput.value = r.entry.schedule;
                    }
                } else {
                    cronStatusLine.innerHTML = "";
                    cronStatusLine.appendChild(h("span", { className: "badge badge-warning" }, t("repl_cron_inactive")));
                    cronCustomInput.value = cronPresetSel.value === "__custom__" ? "" : cronPresetSel.value;
                }
                updatePreview();
            } catch (e) {
                cronStatusLine.textContent = e.message || "";
            }
        }

        cronSaveBtn.onclick = async () => {
            const expr = cronPresetSel.value === "__custom__" ? cronCustomInput.value.trim() : cronPresetSel.value;
            if (!expr || expr.split(/\s+/).length !== 5) {
                toast(t("repl_cron_invalid"), "error"); return;
            }
            cronSaveBtn.disabled = true;
            try {
                const r = await API.post("/api/replication/cron" + qsPair, { schedule: expr, source: src.address });
                if (r.success) { toast(t("repl_cron_saved"), "success"); refreshCron(); }
                else toast(r.error || r.stderr || t("failed"), "error");
            } catch (e) { toast(e.message, "error"); }
            finally { cronSaveBtn.disabled = false; }
        };

        cronRemoveBtn.onclick = async () => {
            if (!confirm(t("repl_cron_remove_confirm"))) return;
            cronRemoveBtn.disabled = true;
            try {
                const r = await API.del("/api/replication/cron" + qsPair, {});
                if (r.success) { toast(t("repl_cron_removed"), "success"); refreshCron(); }
                else toast(r.stderr || t("failed"), "error");
            } catch (e) { toast(e.message, "error"); }
            finally { cronRemoveBtn.disabled = false; }
        };

        refreshCron();

        // -- Step 6: checkzfs status (replication health overview) -------
        const ckCard = h("div", { className: "card", style: "margin-top:16px" });
        const ckRefreshBtn = h("button", { className: "btn btn-sm" }, t("refresh"));
        const ckHeader = h("div", { className: "card-header" }, [h("span", {}, "6. " + t("repl_checkzfs_title")), ckRefreshBtn]);
        ckCard.appendChild(ckHeader);
        const ckBody = h("div", { className: "card-body" });
        ckBody.appendChild(h("p", { style: "font-size:12px;color:var(--text-secondary);margin-bottom:8px" },
            t("repl_checkzfs_desc").replace("{src}", src.name || src.address).replace("{tgt}", tgt.name || tgt.address)));
        const ckSummary = h("div", { style: "margin-bottom:8px;font-size:13px" });
        const ckTableWrap = h("div", { style: "max-height:340px;overflow:auto;border:1px solid var(--border);border-radius:4px;background:var(--bg)" }, t("loading"));
        ckBody.appendChild(ckSummary);
        ckBody.appendChild(ckTableWrap);
        ckCard.appendChild(ckBody);
        logMount.appendChild(ckCard);

        // Filter toggle: show only datasets that actually have a replica
        const ckFilterWrap = h("label", { style: "display:inline-flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;margin-left:8px" });
        const ckFilterCb = h("input", { type: "checkbox", checked: true });
        ckFilterWrap.appendChild(ckFilterCb);
        ckFilterWrap.appendChild(h("span", {}, t("repl_ck_only_replicated")));
        ckSummary.appendChild(ckFilterWrap);

        let ckLastRows = [];

        const stripSourcePrefix = (s) => {
            // checkzfs prints "1.2.3.4#dataset". Drop the "<ip>#" prefix for readability.
            const idx = (s || "").indexOf("#");
            return idx >= 0 ? s.slice(idx + 1) : (s || "");
        };

        const renderCkTable = () => {
            const onlyRepl = ckFilterCb.checked;
            // Group rows by their full source spec ("<ip>#<dataset>"). When the
            // filter is on, keep only groups that contain at least one row with
            // a non-empty replica column. Within a group, render the row with
            // a replica first (it carries the meaningful timestamp).
            const groups = new Map();
            (ckLastRows || []).forEach(row => {
                const key = row.source || "";
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(row);
            });
            const filteredKeys = [];
            groups.forEach((rows, key) => {
                if (onlyRepl) {
                    if (rows.some(r => r.replica && r.replica.length)) filteredKeys.push(key);
                } else {
                    filteredKeys.push(key);
                }
            });

            if (!filteredKeys.length) {
                ckTableWrap.innerHTML = `<p class="muted" style="padding:10px">${escapeHtml(onlyRepl ? t("repl_ck_no_replicas") : t("repl_checkzfs_empty"))}</p>`;
                return;
            }

            const table = h("table", { className: "data-table", style: "margin:0;font-size:12px;width:100%" });
            const thead = h("thead", {}, h("tr", {}, [
                h("th", { style: "width:70px" }, t("repl_ck_status")),
                h("th", {}, t("repl_ck_source")),
                h("th", {}, t("repl_ck_replica")),
                h("th", {}, t("repl_ck_snapshot")),
                h("th", { style: "width:90px;text-align:right" }, t("repl_ck_age")),
                h("th", { style: "width:60px;text-align:right" }, t("repl_ck_count")),
                h("th", {}, t("repl_ck_message")),
            ]));
            table.appendChild(thead);
            const tbody = h("tbody");

            filteredKeys.forEach(key => {
                const rows = groups.get(key);
                // Sort: rows WITH replica first, source-only row second.
                rows.sort((a, b) => (b.replica ? 1 : 0) - (a.replica ? 1 : 0));
                rows.forEach((row, i) => {
                    const stCls = row.status === "ok" ? "badge-success"
                                : row.status === "warn" ? "badge-warning"
                                : row.status === "crit" ? "badge-danger" : "badge-secondary";
                    const stLabel = (row.status || "").toUpperCase();
                    const trStyle = i === 0
                        ? "border-top:1px solid var(--border)"
                        : "background:var(--bg-secondary,rgba(255,255,255,0.02))";
                    const dsCell = stripSourcePrefix(row.source);
                    const tr = h("tr", { style: trStyle }, [
                        h("td", {}, h("span", { className: "badge " + stCls, style: "min-width:48px;text-align:center;display:inline-block" }, stLabel)),
                        h("td", { style: "font-family:monospace;font-size:11px;word-break:break-all" }, dsCell),
                        h("td", { style: "font-family:monospace;font-size:11px;word-break:break-all;color:" + (row.replica ? "var(--success)" : "var(--text-secondary)") },
                            row.replica || "—"),
                        h("td", { style: "font-family:monospace;font-size:11px;word-break:break-all" }, row.snapshot || "—"),
                        h("td", { style: "text-align:right;white-space:nowrap" }, row.age || "—"),
                        h("td", { style: "text-align:right" }, row.count || "0"),
                        h("td", { style: "font-size:11px;color:var(--text-secondary)" }, row.message || ""),
                    ]);
                    tbody.appendChild(tr);
                });
            });

            table.appendChild(tbody);
            ckTableWrap.innerHTML = "";
            ckTableWrap.appendChild(table);
        };

        ckFilterCb.onchange = renderCkTable;

        async function refreshCheckzfs() {
            ckTableWrap.innerHTML = loading();
            // Keep the toggle but reset the pill summary
            const oldPills = ckSummary.querySelectorAll(".badge");
            oldPills.forEach(n => n.remove());
            try {
                const r = await API.get("/api/replication/checkzfs" + qs + "&source=" + encodeURIComponent(src.address));
                if (r.error) {
                    ckTableWrap.innerHTML = `<p class="muted" style="padding:10px">${escapeHtml(r.error)}</p>`;
                    return;
                }
                const s = r.summary || {};
                const mkPill = (label, n, cls) => h("span", { className: "badge " + cls, style: "margin-right:6px" }, label + ": " + (n || 0));
                ckSummary.insertBefore(mkPill("CRIT", s.crit, "badge-danger"), ckFilterWrap);
                ckSummary.insertBefore(mkPill("WARN", s.warn, "badge-warning"), ckSummary.firstChild);
                ckSummary.insertBefore(mkPill("OK",   s.ok,   "badge-success"), ckSummary.firstChild);
                ckLastRows = r.rows || [];
                renderCkTable();
            } catch (e) { ckTableWrap.innerHTML = `<p class="muted" style="padding:10px">${escapeHtml(e.message || "")}</p>`; }
        }
        ckRefreshBtn.onclick = refreshCheckzfs;
        refreshCheckzfs();

        // -- Step 7: Log --------------------------------------------------
        const logCard = h("div", { className: "card", style: "margin-top:16px" });
        const refreshBtn = h("button", { className: "btn btn-sm" }, t("refresh"));
        const logHeader = h("div", { className: "card-header" }, [h("span", {}, "7. " + t("repl_log_title")), refreshBtn]);
        logCard.appendChild(logHeader);
        const logBody = h("div", { className: "card-body" });
        const logPre = h("pre", { style: "background:var(--bg);color:var(--text);padding:10px;border-radius:4px;max-height:400px;overflow:auto;font-size:12px;white-space:pre-wrap" }, t("loading"));
        logBody.appendChild(logPre);
        logCard.appendChild(logBody);
        logMount.appendChild(logCard);

        async function refreshLog() {
            try {
                const r = await API.get("/api/replication/log" + qs + "&lines=300");
                logPre.textContent = r.present ? r.content : t("repl_log_empty");
            } catch (e) { logPre.textContent = e.message || ""; }
        }
        refreshBtn.onclick = refreshLog;
        refreshLog();
    }
}


// -- Notifications ---------------------------------------------------------
async function viewNotifications() {
    setContent(loading());
    const config = await API.get("/api/notifications/config");

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("notifications")),
        h("p", {}, t("notif_subtitle")),
    ]));

    // --- Telegram Card ---
    const tgCard = h("div", { className: "card" });
    tgCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("telegram")),
        h("span", {
            className: `badge ${config.telegram?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.telegram?.enabled ? t("enabled") : t("disabled")),
    ]));
    const tgBody = h("div", { className: "card-body" });
    tgBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="tg-enabled" ${config.telegram?.enabled ? "checked" : ""}>
                ${escapeHtml(t("enable_telegram"))}
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("bot_token"))}</label>
                <input class="form-control" id="tg-token" placeholder="123456:ABC-DEF..." value="${escapeAttr(config.telegram?.bot_token || "")}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("chat_id"))}</label>
                <input class="form-control" id="tg-chat-id" placeholder="-1001234567890" value="${escapeAttr(config.telegram?.chat_id || "")}">
            </div>
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="tg-test-btn">${escapeHtml(t("send_test"))}</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            ${t("tg_help")}
        </p>
    `;
    tgCard.appendChild(tgBody);
    container.appendChild(tgCard);

    // --- Gotify Card ---
    const gtCard = h("div", { className: "card" });
    gtCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("gotify")),
        h("span", {
            className: `badge ${config.gotify?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.gotify?.enabled ? t("enabled") : t("disabled")),
    ]));
    const gtBody = h("div", { className: "card-body" });
    gtBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="gt-enabled" ${config.gotify?.enabled ? "checked" : ""}>
                ${escapeHtml(t("enable_gotify"))}
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("server_url"))}</label>
                <input class="form-control" id="gt-url" placeholder="https://gotify.example.com" value="${escapeAttr(config.gotify?.url || "")}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("app_token"))}</label>
                <input class="form-control" id="gt-token" placeholder="Axxxxxxxxx" value="${escapeAttr(config.gotify?.token || "")}">
            </div>
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="gt-test-btn">${escapeHtml(t("send_test"))}</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            ${t("gt_help")}
        </p>
    `;
    gtCard.appendChild(gtBody);
    container.appendChild(gtCard);

    // --- Matrix Card ---
    const mxCard = h("div", { className: "card" });
    mxCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("matrix")),
        h("span", {
            className: `badge ${config.matrix?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.matrix?.enabled ? t("enabled") : t("disabled")),
    ]));
    const mxBody = h("div", { className: "card-body" });
    mxBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="mx-enabled" ${config.matrix?.enabled ? "checked" : ""}>
                ${escapeHtml(t("enable_matrix"))}
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("homeserver"))}</label>
                <input class="form-control" id="mx-homeserver" placeholder="https://matrix.org" value="${escapeAttr(config.matrix?.homeserver || "")}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("access_token_label"))}</label>
                <input class="form-control" id="mx-token" placeholder="syt_..." value="${escapeAttr(config.matrix?.access_token || "")}">
            </div>
        </div>
        <div class="form-group">
            <label>${escapeHtml(t("room_id"))}</label>
            <input class="form-control" id="mx-room" placeholder="!abc123:matrix.org" value="${escapeAttr(config.matrix?.room_id || "")}">
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="mx-test-btn">${escapeHtml(t("send_test"))}</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            ${t("mx_help")}
        </p>
    `;
    mxCard.appendChild(mxBody);
    container.appendChild(mxCard);

    // --- Email Card ---
    const emCard = h("div", { className: "card" });
    emCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, t("email")),
        h("span", {
            className: `badge ${config.email?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.email?.enabled ? t("enabled") : t("disabled")),
    ]));
    const emBody = h("div", { className: "card-body" });
    const em = config.email || {};
    const sec = em.security || "starttls";
    emBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="em-enabled" ${em.enabled ? "checked" : ""}>
                ${escapeHtml(t("enable_email"))}
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("email_smtp_host"))}</label>
                <input class="form-control" id="em-host" placeholder="smtp.example.com" value="${escapeAttr(em.smtp_host || "")}">
            </div>
            <div class="form-group" style="max-width:140px">
                <label>${escapeHtml(t("email_smtp_port"))}</label>
                <input class="form-control" id="em-port" type="number" min="1" max="65535" value="${em.smtp_port || 587}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("email_security"))}</label>
                <select class="form-control" id="em-security">
                    <option value="starttls" ${sec === "starttls" ? "selected" : ""}>${escapeHtml(t("email_security_starttls"))}</option>
                    <option value="ssl" ${sec === "ssl" ? "selected" : ""}>${escapeHtml(t("email_security_ssl"))}</option>
                    <option value="none" ${sec === "none" ? "selected" : ""}>${escapeHtml(t("email_security_none"))}</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("email_smtp_user"))}</label>
                <input class="form-control" id="em-user" autocomplete="off" value="${escapeAttr(em.smtp_user || "")}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("email_smtp_password"))}</label>
                <input class="form-control" id="em-pass" type="password" autocomplete="new-password" value="${escapeAttr(em.smtp_password || "")}">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>${escapeHtml(t("email_from"))}</label>
                <input class="form-control" id="em-from" placeholder="zfs-tool@example.com" value="${escapeAttr(em.from_address || "")}">
            </div>
            <div class="form-group">
                <label>${escapeHtml(t("email_to"))}</label>
                <input class="form-control" id="em-to" placeholder="admin@example.com, ops@example.com" value="${escapeAttr(em.to_addresses || "")}">
            </div>
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="em-test-btn">${escapeHtml(t("send_test"))}</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            ${escapeHtml(t("email_help"))}<br>
            <span style="opacity:0.8">${escapeHtml(t("email_to_hint"))}</span>
        </p>
    `;
    emCard.appendChild(emBody);
    container.appendChild(emCard);

    // --- Event Configuration ---
    const evCard = h("div", { className: "card" });
    evCard.appendChild(h("div", { className: "card-header" }, t("event_config")));
    const evBody = h("div", { className: "card-body" });

    const eventLabels = {
        scrub_started: t("ev_scrub_started"),
        scrub_finished: t("ev_scrub_finished"),
        rollback: t("ev_rollback"),
        snapshot_created: t("ev_snapshot_created"),
        snapshot_deleted: t("ev_snapshot_deleted"),
        pool_error: t("ev_pool_error"),
        health_warning: t("ev_health_warning"),
        host_offline: t("ev_host_offline"),
        auto_snapshot: t("ev_auto_snapshot"),
    };

    const evGrid = h("div", { className: "grid grid-3" });
    for (const [key, label] of Object.entries(eventLabels)) {
        const checked = config.events?.[key] !== false;
        const item = h("div", { style: "padding:6px 0" });
        item.innerHTML = `
            <label class="checkbox-label">
                <input type="checkbox" class="ev-checkbox" data-event="${key}" ${checked ? "checked" : ""}>
                ${escapeHtml(label)}
            </label>
        `;
        evGrid.appendChild(item);
    }
    evBody.appendChild(evGrid);
    evCard.appendChild(evBody);
    container.appendChild(evCard);

    // --- Save Button ---
    const saveBar = h("div", { style: "margin-top:16px;display:flex;gap:8px" });
    saveBar.appendChild(h("button", { className: "btn btn-primary", id: "notify-save-btn" }, t("save_config")));
    container.appendChild(saveBar);

    setContent(container);

    // --- Wire up buttons ---
    document.getElementById("tg-test-btn").addEventListener("click", async () => {
        const token = document.getElementById("tg-token").value.trim();
        const chatId = document.getElementById("tg-chat-id").value.trim();
        if (!token || !chatId) { toast(t("token_chatid_required"), "error"); return; }
        const r = await API.post("/api/notifications/test/telegram", { bot_token: token, chat_id: chatId });
        toast(r.success ? t("tg_test_sent") : t("tg_failed", r.detail || t("error")),
            r.success ? "success" : "error");
    });

    document.getElementById("gt-test-btn").addEventListener("click", async () => {
        const url = document.getElementById("gt-url").value.trim();
        const token = document.getElementById("gt-token").value.trim();
        if (!url || !token) { toast(t("url_token_required"), "error"); return; }
        const r = await API.post("/api/notifications/test/gotify", { url, token });
        toast(r.success ? t("gt_test_sent") : t("gt_failed", r.detail || t("error")),
            r.success ? "success" : "error");
    });

    document.getElementById("mx-test-btn").addEventListener("click", async () => {
        const homeserver = document.getElementById("mx-homeserver").value.trim();
        const accessToken = document.getElementById("mx-token").value.trim();
        const roomId = document.getElementById("mx-room").value.trim();
        if (!homeserver || !accessToken || !roomId) { toast(t("mx_fields_required"), "error"); return; }
        const r = await API.post("/api/notifications/test/matrix", { homeserver, access_token: accessToken, room_id: roomId });
        toast(r.success ? t("mx_test_sent") : t("mx_failed", r.detail || t("error")),
            r.success ? "success" : "error");
    });

    document.getElementById("em-test-btn").addEventListener("click", async () => {
        const payload = {
            smtp_host: document.getElementById("em-host").value.trim(),
            smtp_port: parseInt(document.getElementById("em-port").value) || 587,
            smtp_user: document.getElementById("em-user").value.trim(),
            smtp_password: document.getElementById("em-pass").value,
            from_address: document.getElementById("em-from").value.trim(),
            to_addresses: document.getElementById("em-to").value.trim(),
            security: document.getElementById("em-security").value,
        };
        if (!payload.smtp_host || !payload.from_address || !payload.to_addresses) {
            toast(t("email_fields_required"), "error"); return;
        }
        const r = await API.post("/api/notifications/test/email", payload);
        toast(r.success ? t("email_test_sent") : t("email_failed", r.detail || t("error")),
            r.success ? "success" : "error");
    });

    document.getElementById("notify-save-btn").addEventListener("click", async () => {
        const events = {};
        document.querySelectorAll(".ev-checkbox").forEach(cb => {
            events[cb.dataset.event] = cb.checked;
        });
        const newConfig = {
            telegram: {
                enabled: document.getElementById("tg-enabled").checked,
                bot_token: document.getElementById("tg-token").value.trim(),
                chat_id: document.getElementById("tg-chat-id").value.trim(),
            },
            gotify: {
                enabled: document.getElementById("gt-enabled").checked,
                url: document.getElementById("gt-url").value.trim(),
                token: document.getElementById("gt-token").value.trim(),
            },
            matrix: {
                enabled: document.getElementById("mx-enabled").checked,
                homeserver: document.getElementById("mx-homeserver").value.trim(),
                access_token: document.getElementById("mx-token").value.trim(),
                room_id: document.getElementById("mx-room").value.trim(),
            },
            email: {
                enabled: document.getElementById("em-enabled").checked,
                smtp_host: document.getElementById("em-host").value.trim(),
                smtp_port: parseInt(document.getElementById("em-port").value) || 587,
                smtp_user: document.getElementById("em-user").value.trim(),
                smtp_password: document.getElementById("em-pass").value,
                from_address: document.getElementById("em-from").value.trim(),
                to_addresses: document.getElementById("em-to").value.trim(),
                security: document.getElementById("em-security").value,
            },
            events,
        };
        const r = await API.post("/api/notifications/config", newConfig);
        toast(r.message || t("saved"), r.success ? "success" : "error");
        viewNotifications();
    });
}

// ---------------------------------------------------------------------------
// Simple markdown renderer for AI reports
// ---------------------------------------------------------------------------
function renderMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);
    // Code blocks
    html = html.replace(/```([\s\S]*?)```/g, '<pre style="background:var(--bg-secondary);padding:12px;border-radius:6px;overflow-x:auto;font-size:13px"><code>$1</code></pre>');
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Headers
    html = html.replace(/^#### (.+)$/gm, '<h5 style="color:var(--accent);margin:12px 0 4px">$1</h5>');
    html = html.replace(/^### (.+)$/gm, '<h4 style="color:var(--accent);margin:16px 0 6px">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 style="color:var(--accent);margin:18px 0 8px">$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h3 style="color:var(--accent);margin:20px 0 8px;font-size:1.3em">$1</h3>');
    // Horizontal rule
    html = html.replace(/^---$/gm, '<hr style="border-color:var(--border);margin:16px 0">');
    // Bullet lists
    html = html.replace(/^[\-\*] (.+)$/gm, '<li style="margin-left:18px;line-height:1.8">$1</li>');
    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li style="margin-left:18px;line-height:1.8;list-style-type:decimal">$1</li>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    // Clean up <br> after block elements
    html = html.replace(/<\/h[345]><br>/g, '</h3>');
    html = html.replace(/<\/li><br>/g, '</li>');
    html = html.replace(/<\/pre><br>/g, '</pre>');
    html = html.replace(/<hr[^>]*><br>/g, '<hr style="border-color:var(--border);margin:16px 0">');
    return html;
}

// ---------------------------------------------------------------------------
// AI Reports view
// ---------------------------------------------------------------------------
async function viewAI() {
    if (!currentHost) {
        setContent(h("div", { className: "page-header" }, [
            h("h2", {}, t("ai_reports")),
            h("p", { style: "color:var(--text-secondary)" }, t("select_host_first")),
        ]));
        return;
    }

    setContent(loading());
    const config = await API.get("/api/ai/config");
    const reports = await API.get("/api/ai/reports");

    const container = h("div");

    // Header
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("ai_reports")),
        h("p", {}, t("ai_subtitle")),
    ]));

    // ---- Card 1: Provider Configuration ----
    const provCard = h("div", { className: "card" });
    provCard.appendChild(h("div", { className: "card-header" }, t("ai_provider_config")));
    const provBody = h("div", { className: "card-body" });

    const activeProvider = config.provider || "openai";
    const oai = config.openai || {};
    const ant = config.anthropic || {};
    const oll = config.ollama || {};
    const cust = config.custom || {};

    provBody.innerHTML = `
        <div class="grid grid-2" style="gap:16px">
            <div style="grid-column:1/-1">
                <label>${escapeHtml(t("ai_provider"))}</label>
                <select id="ai-provider" class="form-control" style="margin-top:4px">
                    <option value="openai" ${activeProvider === "openai" ? "selected" : ""}>${escapeHtml(t("ai_provider_openai"))}</option>
                    <option value="anthropic" ${activeProvider === "anthropic" ? "selected" : ""}>${escapeHtml(t("ai_provider_anthropic"))}</option>
                    <option value="ollama" ${activeProvider === "ollama" ? "selected" : ""}>${escapeHtml(t("ai_provider_ollama"))}</option>
                    <option value="custom" ${activeProvider === "custom" ? "selected" : ""}>${escapeHtml(t("ai_provider_custom"))}</option>
                </select>
            </div>

            <!-- OpenAI fields -->
            <div id="ai-fields-openai" class="ai-provider-fields" style="grid-column:1/-1;display:${activeProvider === "openai" ? "block" : "none"}">
                <div class="grid grid-2" style="gap:12px">
                    <div>
                        <label>${escapeHtml(t("ai_api_key"))}</label>
                        <input id="ai-oai-key" class="form-control" type="password" value="${escapeAttr(oai.api_key || "")}" style="margin-top:4px">
                    </div>
                    <div>
                        <label>${escapeHtml(t("ai_model"))}</label>
                        <input id="ai-oai-model" class="form-control" value="${escapeAttr(oai.model || "gpt-4o-mini")}" style="margin-top:4px">
                    </div>
                    <div style="grid-column:1/-1">
                        <label>${escapeHtml(t("ai_base_url"))}</label>
                        <input id="ai-oai-url" class="form-control" value="${escapeAttr(oai.base_url || "https://api.openai.com/v1")}" style="margin-top:4px">
                    </div>
                </div>
            </div>

            <!-- Anthropic fields -->
            <div id="ai-fields-anthropic" class="ai-provider-fields" style="grid-column:1/-1;display:${activeProvider === "anthropic" ? "block" : "none"}">
                <div class="grid grid-2" style="gap:12px">
                    <div>
                        <label>${escapeHtml(t("ai_api_key"))}</label>
                        <input id="ai-ant-key" class="form-control" type="password" value="${escapeAttr(ant.api_key || "")}" style="margin-top:4px">
                    </div>
                    <div>
                        <label>${escapeHtml(t("ai_model"))}</label>
                        <input id="ai-ant-model" class="form-control" value="${escapeAttr(ant.model || "claude-sonnet-4-20250514")}" style="margin-top:4px">
                    </div>
                </div>
            </div>

            <!-- Ollama fields -->
            <div id="ai-fields-ollama" class="ai-provider-fields" style="grid-column:1/-1;display:${activeProvider === "ollama" ? "block" : "none"}">
                <div class="grid grid-2" style="gap:12px">
                    <div>
                        <label>${escapeHtml(t("ai_base_url"))}</label>
                        <input id="ai-oll-url" class="form-control" value="${escapeAttr(oll.base_url || "http://localhost:11434")}" style="margin-top:4px">
                    </div>
                    <div>
                        <label>${escapeHtml(t("ai_model"))}</label>
                        <div style="display:flex;gap:6px;margin-top:4px">
                            <select id="ai-oll-model" class="form-control" style="flex:1">
                                <option value="${escapeAttr(oll.model || "llama3")}" selected>${escapeHtml(oll.model || "llama3")}</option>
                            </select>
                            <button class="btn btn-sm" id="ai-oll-refresh" title="${escapeAttr(t("ai_ollama_refresh"))}" style="white-space:nowrap">${escapeHtml(t("ai_ollama_refresh"))}</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Custom fields -->
            <div id="ai-fields-custom" class="ai-provider-fields" style="grid-column:1/-1;display:${activeProvider === "custom" ? "block" : "none"}">
                <div class="grid grid-2" style="gap:12px">
                    <div style="grid-column:1/-1">
                        <label>${escapeHtml(t("ai_base_url"))}</label>
                        <input id="ai-cust-url" class="form-control" value="${escapeAttr(cust.base_url || "")}" placeholder="https://your-api.example.com/v1" style="margin-top:4px">
                    </div>
                    <div>
                        <label>${escapeHtml(t("ai_api_key"))}</label>
                        <input id="ai-cust-key" class="form-control" type="password" value="${escapeAttr(cust.api_key || "")}" style="margin-top:4px">
                    </div>
                    <div>
                        <label>${escapeHtml(t("ai_model"))}</label>
                        <input id="ai-cust-model" class="form-control" value="${escapeAttr(cust.model || "")}" style="margin-top:4px">
                    </div>
                </div>
            </div>

            <!-- System prompt -->
            <div style="grid-column:1/-1">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <label>${escapeHtml(t("ai_system_prompt"))}</label>
                    <button class="btn btn-xs" id="ai-prompt-reset" style="font-size:11px;padding:2px 8px">${escapeHtml(t("ai_prompt_reset"))}</button>
                </div>
                <textarea id="ai-system-prompt" class="form-control" rows="6" style="margin-top:4px;font-size:12px;font-family:monospace">${escapeHtml((() => {
                            const sp = (config.system_prompt || "").trim();
                            const defEn = (config.default_system_prompt_en || "").trim();
                            const defDe = (config.default_system_prompt_de || "").trim();
                            if (!sp || sp === defEn || sp === defDe) return getLang() === "de" ? defDe : defEn;
                            return sp;
                        })())}</textarea>
                <small style="color:var(--text-secondary)">${escapeHtml(t("ai_system_prompt_hint"))}</small>
            </div>
        </div>

        <div style="margin-top:16px;display:flex;gap:10px">
            <button class="btn btn-sm" id="ai-test-btn">${escapeHtml(t("ai_test_connection"))}</button>
            <button class="btn btn-sm btn-primary" id="ai-save-btn">${escapeHtml(t("ai_save_config"))}</button>
            <span id="ai-test-result" style="font-size:13px;padding-top:6px"></span>
        </div>
    `;
    provCard.appendChild(provBody);
    container.appendChild(provCard);

    // ---- Card 2: Schedule & Generate ----
    const schedCard = h("div", { className: "card", style: "margin-top:16px" });
    schedCard.appendChild(h("div", { className: "card-header" }, t("ai_schedule")));
    const schedBody = h("div", { className: "card-body" });

    const sched = config.schedule || {};
    const schedules = config.schedules || {};
    const hostSched = schedules[currentHost] || { enabled: false, interval: "daily", hour: 6, weekday: 0 };
    const weekdays = t("ai_weekdays").split(",");
    const mkWeekdayOpts = (sel) => weekdays.map((d, i) =>
        `<option value="${i}" ${sel === i ? "selected" : ""}>${escapeHtml(d)}</option>`
    ).join("");

    schedBody.innerHTML = `
        <p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px">
            ${escapeHtml(t("ai_schedule_note"))}
        </p>

        <!-- Combined all-hosts schedule -->
        <fieldset style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:14px">
            <legend style="padding:0 8px;color:var(--accent);font-weight:600">${escapeHtml(t("ai_schedule_all_hosts"))}</legend>
            <div class="grid grid-2" style="gap:14px">
                <div style="grid-column:1/-1">
                    <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                        <input type="checkbox" id="ai-sched-enabled" ${sched.enabled ? "checked" : ""}>
                        ${escapeHtml(t("ai_schedule_enable"))}
                    </label>
                </div>
                <div>
                    <label>${escapeHtml(t("ai_schedule_interval"))}</label>
                    <select id="ai-sched-interval" class="form-control" style="margin-top:4px">
                        <option value="daily" ${sched.interval === "daily" ? "selected" : ""}>${escapeHtml(t("ai_schedule_daily"))}</option>
                        <option value="weekly" ${sched.interval === "weekly" ? "selected" : ""}>${escapeHtml(t("ai_schedule_weekly"))}</option>
                    </select>
                </div>
                <div>
                    <label>${escapeHtml(t("ai_schedule_hour"))}</label>
                    <input id="ai-sched-hour" class="form-control" type="number" min="0" max="23" value="${sched.hour ?? 6}" style="margin-top:4px">
                </div>
                <div id="ai-weekday-row" style="${sched.interval === "weekly" ? "" : "display:none"}">
                    <label>${escapeHtml(t("ai_schedule_weekday"))}</label>
                    <select id="ai-sched-weekday" class="form-control" style="margin-top:4px">${mkWeekdayOpts(sched.weekday)}</select>
                </div>
            </div>
        </fieldset>

        <!-- Per-host schedule -->
        <fieldset style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:14px">
            <legend style="padding:0 8px;color:var(--accent);font-weight:600">${escapeHtml(t("ai_schedule_this_host"))} &mdash; <code>${escapeHtml(currentHost || "?")}</code></legend>
            <div class="grid grid-2" style="gap:14px">
                <div style="grid-column:1/-1">
                    <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                        <input type="checkbox" id="ai-hsched-enabled" ${hostSched.enabled ? "checked" : ""}>
                        ${escapeHtml(t("ai_schedule_enable"))}
                    </label>
                </div>
                <div>
                    <label>${escapeHtml(t("ai_schedule_interval"))}</label>
                    <select id="ai-hsched-interval" class="form-control" style="margin-top:4px">
                        <option value="daily" ${hostSched.interval === "daily" ? "selected" : ""}>${escapeHtml(t("ai_schedule_daily"))}</option>
                        <option value="weekly" ${hostSched.interval === "weekly" ? "selected" : ""}>${escapeHtml(t("ai_schedule_weekly"))}</option>
                    </select>
                </div>
                <div>
                    <label>${escapeHtml(t("ai_schedule_hour"))}</label>
                    <input id="ai-hsched-hour" class="form-control" type="number" min="0" max="23" value="${hostSched.hour ?? 6}" style="margin-top:4px">
                </div>
                <div id="ai-hweekday-row" style="${hostSched.interval === "weekly" ? "" : "display:none"}">
                    <label>${escapeHtml(t("ai_schedule_weekday"))}</label>
                    <select id="ai-hsched-weekday" class="form-control" style="margin-top:4px">${mkWeekdayOpts(hostSched.weekday)}</select>
                </div>
            </div>
        </fieldset>

        <div style="margin-top:8px">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" id="ai-notify-report" ${config.notify_on_report ? "checked" : ""}>
                ${escapeHtml(t("ai_notify_on_report"))}
            </label>
        </div>
        <div style="margin-top:6px">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" id="ai-attach-pdf" ${config.attach_pdf !== false ? "checked" : ""}>
                ${escapeHtml(t("ai_attach_pdf"))}
            </label>
        </div>

        <div style="margin-top:16px;display:flex;gap:8px;align-items:center">
            <button class="btn btn-primary" id="ai-generate-btn">${escapeHtml(t("ai_generate_now"))}</button>
            <button class="btn" id="ai-export-raw-btn">${escapeHtml(t("ai_export_raw") || "Export Raw Data")}</button>
            <span id="ai-generate-status" style="font-size:13px;margin-left:10px"></span>
        </div>
    `;
    schedCard.appendChild(schedBody);
    container.appendChild(schedCard);

    // ---- Card 3: Reports & Chat ----
    // Filter reports by current host
    const hostReports = reports.filter(r => {
        if (!r.host_addresses) return true; // old reports without host info
        return r.host_addresses.includes(currentHost);
    });

    const reportCard = h("div", { className: "card", style: "margin-top:16px" });
    reportCard.appendChild(h("div", { className: "card-header" }, t("ai_report_viewer")));
    const reportBody = h("div", { className: "card-body" });

    if (hostReports.length === 0) {
        reportBody.innerHTML = `<p style="color:var(--text-secondary)">${escapeHtml(t("ai_no_reports"))}</p>`;
    } else {
        let reportOpts = hostReports.map((r, i) =>
            `<option value="${i}">${escapeHtml(r.timestamp)} — ${escapeHtml(r.provider)} (${escapeHtml(r.model)})</option>`
        ).join("");

        const firstReport = hostReports[0];
        const metaText = t("ai_report_meta", firstReport.timestamp, firstReport.provider, firstReport.model, firstReport.host_count || "?");

        reportBody.innerHTML = `
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px">
                <select id="ai-report-select" class="form-control" style="flex:1">
                    ${reportOpts}
                </select>
                <button class="btn btn-sm" id="ai-pdf-btn" title="${escapeAttr(t("ai_download_pdf"))}">${escapeHtml(t("ai_download_pdf"))}</button>
            </div>
            <div id="ai-report-meta" style="font-size:12px;color:var(--text-secondary);margin-bottom:12px">${escapeHtml(metaText)}</div>
            <div id="ai-report-content" style="line-height:1.7;font-size:14px">${renderMarkdown(firstReport.content)}</div>

            <hr style="border-color:var(--border);margin:24px 0 16px">

            <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("ai_chat_title"))}</h4>
            <div style="display:flex;gap:8px">
                <input id="ai-chat-input" class="form-control" style="flex:1" placeholder="${escapeAttr(t("ai_chat_placeholder"))}">
                <button class="btn btn-primary" id="ai-chat-btn">${escapeHtml(t("ai_chat_send"))}</button>
            </div>
            <div id="ai-chat-response" style="margin-top:12px;font-size:14px;line-height:1.7"></div>
        `;
    }
    reportCard.appendChild(reportBody);
    container.appendChild(reportCard);

    setContent(container);

    // ---- Wire up event listeners ----

    // Provider toggle
    document.getElementById("ai-provider").addEventListener("change", (e) => {
        document.querySelectorAll(".ai-provider-fields").forEach(el => el.style.display = "none");
        const target = document.getElementById(`ai-fields-${e.target.value}`);
        if (target) target.style.display = "block";
    });

    // Weekly/daily toggle
    document.getElementById("ai-sched-interval").addEventListener("change", (e) => {
        document.getElementById("ai-weekday-row").style.display = e.target.value === "weekly" ? "" : "none";
    });
    document.getElementById("ai-hsched-interval").addEventListener("change", (e) => {
        document.getElementById("ai-hweekday-row").style.display = e.target.value === "weekly" ? "" : "none";
    });

    // Ollama model refresh
    document.getElementById("ai-oll-refresh").addEventListener("click", async () => {
        const btn = document.getElementById("ai-oll-refresh");
        const select = document.getElementById("ai-oll-model");
        const currentModel = select.value;
        btn.disabled = true;
        btn.textContent = "...";
        const baseUrl = document.getElementById("ai-oll-url").value.trim();
        const r = await API.post("/api/ai/ollama-models", { base_url: baseUrl });
        btn.disabled = false;
        btn.textContent = t("ai_ollama_refresh");
        if (r.success && r.models && r.models.length > 0) {
            select.innerHTML = r.models.map(m =>
                `<option value="${escapeAttr(m)}" ${m === currentModel ? "selected" : ""}>${escapeHtml(m)}</option>`
            ).join("");
            toast(t("ai_ollama_models_found", r.models.length), "success");
        } else {
            toast(t("ai_ollama_models_failed", r.error || "No models"), "error");
        }
    });

    // Test connection
    document.getElementById("ai-test-btn").addEventListener("click", async () => {
        const resultEl = document.getElementById("ai-test-result");
        resultEl.textContent = t("ai_testing");
        resultEl.style.color = "var(--text-secondary)";
        try {
            // Save config first so test uses current values
            await _saveAIConfig();
            const r = await API.post("/api/ai/test", {});
            if (r.success) {
                resultEl.textContent = t("ai_test_success", r.message || "OK");
                resultEl.style.color = "var(--success)";
            } else {
                resultEl.textContent = t("ai_test_failed", r.message || r.error || "Unknown");
                resultEl.style.color = "var(--danger)";
            }
        } catch (e) {
            resultEl.textContent = t("ai_test_failed", e.message || "Request failed");
            resultEl.style.color = "var(--danger)";
        }
    });

    // Save config
    document.getElementById("ai-save-btn").addEventListener("click", async () => {
        await _saveAIConfig();
        toast(t("ai_config_saved"), "success");
    });

    // Reset system prompt to default
    document.getElementById("ai-prompt-reset").addEventListener("click", () => {
        const promptEl = document.getElementById("ai-system-prompt");
        promptEl.value = (getLang() === "de" ? config.default_system_prompt_de : config.default_system_prompt_en) || "";
    });

    // Export raw data
    document.getElementById("ai-export-raw-btn").addEventListener("click", () => {
        window.open(`/api/ai/raw-data?host=${encodeURIComponent(currentHost)}`, "_blank");
    });

    // Generate report
    document.getElementById("ai-generate-btn").addEventListener("click", async () => {
        const btn = document.getElementById("ai-generate-btn");
        const statusEl = document.getElementById("ai-generate-status");
        btn.disabled = true;
        statusEl.textContent = t("ai_generating");
        statusEl.style.color = "var(--text-secondary)";

        // Save config first so language/provider changes are applied
        await _saveAIConfig();

        const lang = getLang();
        const r = await API.post("/api/ai/report", { host: currentHost, lang });
        btn.disabled = false;
        if (r.success) {
            toast(t("ai_report_generated"), "success");
            viewAI(); // Refresh to show new report
        } else {
            statusEl.textContent = t("ai_report_failed", r.error || "Unknown");
            statusEl.style.color = "var(--danger)";
        }
    });

    // Report selector + PDF
    if (hostReports.length > 0) {
        document.getElementById("ai-report-select").addEventListener("change", (e) => {
            const idx = parseInt(e.target.value);
            const rep = hostReports[idx];
            if (rep) {
                document.getElementById("ai-report-content").innerHTML = renderMarkdown(rep.content);
                document.getElementById("ai-report-meta").textContent =
                    t("ai_report_meta", rep.timestamp, rep.provider, rep.model, rep.host_count || "?");
            }
        });

        // PDF download
        document.getElementById("ai-pdf-btn").addEventListener("click", () => {
            const idx = parseInt(document.getElementById("ai-report-select").value);
            const rep = hostReports[idx];
            if (rep && rep.id) {
                window.open(`/api/ai/report/pdf/${rep.id}`, "_blank");
            }
        });

        // Chat
        const chatBtn = document.getElementById("ai-chat-btn");
        const chatInput = document.getElementById("ai-chat-input");
        if (chatBtn) {
            const sendChat = async () => {
                const question = chatInput.value.trim();
                if (!question) { toast(t("ai_chat_empty"), "error"); return; }
                const responseEl = document.getElementById("ai-chat-response");
                responseEl.innerHTML = `<span style="color:var(--text-secondary)">${escapeHtml(t("ai_chat_thinking"))}</span>`;
                chatBtn.disabled = true;

                const lang = getLang();
                const r = await API.post("/api/ai/chat", { question, host: currentHost, lang });
                chatBtn.disabled = false;
                if (r.success) {
                    responseEl.innerHTML = renderMarkdown(r.answer);
                } else {
                    responseEl.innerHTML = `<span style="color:var(--danger)">${escapeHtml(t("ai_chat_failed", r.error || "Unknown"))}</span>`;
                }
            };
            chatBtn.addEventListener("click", sendChat);
            chatInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter") sendChat();
            });
        }
    }
}

async function _saveAIConfig() {
    const provider = document.getElementById("ai-provider").value;

    // Build per-host schedule patch. We only touch the currentHost entry so
    // schedules for other hosts are preserved via a server-side merge.
    const existing = await API.get("/api/ai/config");
    const schedules = existing.schedules || {};
    const hostEnabled = document.getElementById("ai-hsched-enabled").checked;
    const hostCfg = {
        enabled: hostEnabled,
        interval: document.getElementById("ai-hsched-interval").value,
        hour: parseInt(document.getElementById("ai-hsched-hour").value) || 6,
        weekday: parseInt(document.getElementById("ai-hsched-weekday").value) || 0,
    };
    if (hostEnabled) {
        schedules[currentHost] = hostCfg;
    } else {
        // Keep disabled entry so UI values persist, but mark disabled
        schedules[currentHost] = hostCfg;
    }

    const newConfig = {
        provider,
        openai: {
            api_key: document.getElementById("ai-oai-key").value.trim(),
            model: document.getElementById("ai-oai-model").value.trim(),
            base_url: document.getElementById("ai-oai-url").value.trim(),
        },
        anthropic: {
            api_key: document.getElementById("ai-ant-key").value.trim(),
            model: document.getElementById("ai-ant-model").value.trim(),
        },
        ollama: {
            base_url: document.getElementById("ai-oll-url").value.trim(),
            model: document.getElementById("ai-oll-model").value.trim(),
        },
        custom: {
            base_url: document.getElementById("ai-cust-url").value.trim(),
            api_key: document.getElementById("ai-cust-key").value.trim(),
            model: document.getElementById("ai-cust-model").value.trim(),
        },
        schedule: {
            enabled: document.getElementById("ai-sched-enabled").checked,
            interval: document.getElementById("ai-sched-interval").value,
            hour: parseInt(document.getElementById("ai-sched-hour").value) || 6,
            weekday: parseInt(document.getElementById("ai-sched-weekday").value) || 0,
        },
        schedules,
        report_language: getLang(),
        notify_on_report: document.getElementById("ai-notify-report").checked,
        attach_pdf: document.getElementById("ai-attach-pdf").checked,
        system_prompt: document.getElementById("ai-system-prompt").value.trim(),
    };
    await API.post("/api/ai/config", newConfig);
}

function escapeAttr(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------
function openModal(title, bodyHtml, onConfirm) {
    const overlay = document.getElementById("modal-overlay");
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = bodyHtml;

    const footer = document.getElementById("modal-footer");
    footer.innerHTML = "";
    footer.appendChild(h("button", { className: "btn", onClick: closeModal }, t("close")));
    if (onConfirm) {
        footer.appendChild(h("button", { className: "btn btn-primary", onClick: onConfirm }, t("confirm")));
    }
    overlay.classList.add("active");
}

function closeModal() {
    document.getElementById("modal-overlay").classList.remove("active");
    // Always unmount LXC restore session when modal closes
    if (_restoreSession && !_restoreSession._isZvol) {
        const session = _restoreSession;
        _restoreSession = null;
        API.post("/api/restore/unmount", { host: currentHost, clone_ds: session.clone_ds })
            .then(() => toast(t("restore_unmounted"), "success"))
            .catch(() => {});
    }
    // Always unmount Zvol restore session when modal closes
    if (_zvolRestoreSession) {
        const sess = _zvolRestoreSession;
        _zvolRestoreSession = null;
        _restoreSession = null;
        const payload = { host: currentHost, mount_path: sess.mount_path || "", zvol_dev: sess.zvol_dev || "" };
        API.post("/api/restore/zvol/unmount", payload)
            .then(() => toast(t("unmounted") || "Unmounted & cleaned up", "success"))
            .catch(() => {});
    }
}

function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Host selector
// ---------------------------------------------------------------------------
async function loadHostSelector() {
    const hosts = await API.get("/api/hosts");
    const sel = document.getElementById("host-select");
    sel.innerHTML = `<option value="">${t("select_host")}</option>`;
    for (const h of hosts) {
        const opt = document.createElement("option");
        opt.value = h.address;
        opt.textContent = `${h.name} (${h.address})`;
        sel.appendChild(opt);
    }
    if (currentHost) sel.value = currentHost;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
// Cleanup zvol sessions on tab close / refresh (best-effort, sendBeacon)
window.addEventListener("beforeunload", () => {
    if (_zvolRestoreSession) {
        const payload = JSON.stringify({
            host: currentHost,
            mount_path: _zvolRestoreSession.mount_path || "",
            zvol_dev: _zvolRestoreSession.zvol_dev || "",
        });
        navigator.sendBeacon("/api/restore/zvol/unmount", new Blob([payload], { type: "application/json" }));
    }
});

document.addEventListener("DOMContentLoaded", async () => {
    // Fetch CSRF token if not in sessionStorage (e.g. after page refresh)
    if (!_csrfToken) {
        try {
            const r = await fetch("/api/csrf-token");
            if (r.ok) {
                const data = await r.json();
                _csrfToken = data.csrf_token || "";
                sessionStorage.setItem("csrf_token", _csrfToken);
            }
        } catch (e) { /* will redirect to login if not authenticated */ }
    }

    // Set language selector to stored preference
    const langSel = document.getElementById("lang-select");
    langSel.value = getLang();
    updateSidebarLanguage();

    loadHostSelector();

    document.getElementById("host-select").addEventListener("change", (e) => {
        currentHost = e.target.value || null;
        if (currentView !== "home" && currentView !== "hosts") renderView();
    });

    langSel.addEventListener("change", (e) => {
        setLang(e.target.value);
        updateSidebarLanguage();
        renderView(); // Re-render current view with new language
    });

    document.querySelectorAll(".nav-item").forEach(el => {
        el.addEventListener("click", () => navigate(el.dataset.view));
    });

    document.getElementById("modal-overlay").addEventListener("click", (e) => {
        if (e.target === e.currentTarget) closeModal();
    });

    // Logout button
    document.getElementById("logout-btn").addEventListener("click", async () => {
        if (!confirm(t("logout_confirm"))) return;
        await API.post("/api/logout", {});
        window.location.href = "/login";
    });

    navigate("home");
});
