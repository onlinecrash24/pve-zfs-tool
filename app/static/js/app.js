// ---------------------------------------------------------------------------
// ZFS Tool – Frontend Application
// ---------------------------------------------------------------------------

const API = {
    async _handle(r) {
        if (r.status === 401) { window.location.href = "/login"; throw new Error("Not authenticated"); }
        return r.json();
    },
    async get(url) {
        const r = await fetch(url);
        return this._handle(r);
    },
    async post(url, data) {
        const r = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        return this._handle(r);
    },
    async del(url, data) {
        const r = await fetch(url, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
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
        guests: viewGuests,
        health: viewHealth,
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
        h("button", { className: "btn btn-sm btn-primary", onClick: () => copyKey(key.key) }, t("copy")),
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

    // Quick stats
    if (hosts.length > 0) {
        const statsGrid = h("div", { className: "grid grid-3", style: "margin-top:16px" });
        statsGrid.appendChild(makeStatCard(t("hosts"), hosts.length, ""));
        statsGrid.appendChild(makeStatCard(t("status"), t("active"), ""));

        let totalPools = 0;
        for (const host of hosts) {
            try {
                const pools = await API.get(`/api/pools?host=${host.address}`);
                totalPools += pools.length;
            } catch (e) { /* skip */ }
        }
        statsGrid.appendChild(makeStatCard(t("total_pools"), totalPools, ""));
        container.appendChild(statsGrid);
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
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_health_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_health_1"))}</li>
                    <li>${escapeHtml(t("feat_health_2"))}</li>
                    <li>${escapeHtml(t("feat_health_3"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_notify_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_notify_1"))}</li>
                    <li>${escapeHtml(t("feat_notify_2"))}</li>
                    <li>${escapeHtml(t("feat_notify_3"))}</li>
                </ul>
            </div>
            <div>
                <h4 style="margin-bottom:8px;color:var(--accent)">${escapeHtml(t("feat_ai_title"))}</h4>
                <ul style="font-size:13px;color:var(--text-secondary);line-height:1.8;padding-left:18px">
                    <li>${escapeHtml(t("feat_ai_1"))}</li>
                    <li>${escapeHtml(t("feat_ai_2"))}</li>
                    <li>${escapeHtml(t("feat_ai_3"))}</li>
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

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, t("zfs_datasets")),
        h("p", {}, t("datasets_on", currentHost)),
    ]));

    const headerActions = h("div", { style: "margin-bottom:16px" });
    headerActions.appendChild(h("button", { className: "btn btn-primary", onClick: showCreateDatasetForm }, t("create_dataset")));
    container.appendChild(headerActions);

    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, `${t("datasets")} (${datasets.length})`));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, t("name")), h("th", {}, t("type")), h("th", {}, t("used")),
        h("th", {}, t("avail")), h("th", {}, t("refer")), h("th", {}, t("compress")),
        h("th", {}, t("ratio")), h("th", {}, t("actions")),
    ])));
    const tbody = h("tbody");
    for (const ds of datasets) {
        const tr = h("tr");
        tr.appendChild(h("td", {}, h("strong", {}, ds.name)));
        tr.appendChild(h("td", {}, ds.type));
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
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableCard.appendChild(table);
    container.appendChild(tableCard);
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

    // Events
    const evCard = h("div", { className: "card" });
    evCard.appendChild(h("div", { className: "card-header" }, t("recent_events")));
    evCard.appendChild(h("div", { className: "card-body" }, [
        h("pre", { className: "output" }, events.stdout || events.stderr || t("no_events")),
    ]));
    container.appendChild(evCard);

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

    setContent(container);
}

function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
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
    const weekdays = t("ai_weekdays").split(",");
    let weekdayOpts = weekdays.map((d, i) =>
        `<option value="${i}" ${sched.weekday === i ? "selected" : ""}>${escapeHtml(d)}</option>`
    ).join("");

    schedBody.innerHTML = `
        <div class="grid grid-2" style="gap:16px">
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
                <select id="ai-sched-weekday" class="form-control" style="margin-top:4px">${weekdayOpts}</select>
            </div>
            <div style="grid-column:1/-1">
                <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                    <input type="checkbox" id="ai-notify-report" ${config.notify_on_report ? "checked" : ""}>
                    ${escapeHtml(t("ai_notify_on_report"))}
                </label>
            </div>
        </div>

        <div style="margin-top:16px">
            <button class="btn btn-primary" id="ai-generate-btn">${escapeHtml(t("ai_generate_now"))}</button>
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
        report_language: getLang(),
        notify_on_report: document.getElementById("ai-notify-report").checked,
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
    // Always unmount restore session when modal closes
    if (_restoreSession) {
        const session = _restoreSession;
        _restoreSession = null;
        API.post("/api/restore/unmount", { host: currentHost, clone_ds: session.clone_ds })
            .then(() => toast(t("restore_unmounted"), "success"))
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
document.addEventListener("DOMContentLoaded", () => {
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
