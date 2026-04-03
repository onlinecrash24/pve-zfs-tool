// ---------------------------------------------------------------------------
// ZFS Tool – Frontend Application
// ---------------------------------------------------------------------------

const API = {
    async get(url) {
        const r = await fetch(url);
        return r.json();
    },
    async post(url, data) {
        const r = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        return r.json();
    },
    async del(url, data) {
        const r = await fetch(url, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        return r.json();
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
    const t = document.createElement("div");
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 4000);
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
        toast("Please select a host first", "error");
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
    return '<div class="loading-placeholder"><span class="spinner"></span> Loading...</div>';
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
        autosnap: viewAutoSnapshot,
        health: viewHealth,
        notifications: viewNotifications,
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
        h("h2", {}, "ZFS Tool for Proxmox VE"),
        h("p", {}, "Manage ZFS pools, snapshots, and auto-snapshot across your Proxmox hosts."),
    ]));

    // SSH Key
    const keyCard = h("div", { className: "card" });
    keyCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, "SSH Public Key"),
        h("button", { className: "btn btn-sm", onClick: () => copyKey(key.key) }, "Copy"),
    ]));
    const keyBody = h("div", { className: "card-body" });
    if (key.key) {
        const pre = h("div", { className: "key-display" }, key.key);
        keyBody.appendChild(pre);
        keyBody.appendChild(h("p", {
            className: "",
            style: "margin-top:10px;font-size:13px;color:var(--text-secondary)"
        }, 'Add this key to ~/.ssh/authorized_keys on your Proxmox hosts to enable SSH access.'));
    } else {
        keyBody.appendChild(h("p", {}, "No SSH key found."));
    }
    keyCard.appendChild(keyBody);
    container.appendChild(keyCard);

    // Quick stats
    if (hosts.length > 0) {
        const statsGrid = h("div", { className: "grid grid-3", style: "margin-top:16px" });
        statsGrid.appendChild(makeStatCard("Hosts", hosts.length, ""));
        statsGrid.appendChild(makeStatCard("Status", "Active", ""));

        let totalPools = 0;
        for (const host of hosts) {
            try {
                const pools = await API.get(`/api/pools?host=${host.address}`);
                totalPools += pools.length;
            } catch (e) { /* skip */ }
        }
        statsGrid.appendChild(makeStatCard("Total Pools", totalPools, ""));
        container.appendChild(statsGrid);
    }

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
    navigator.clipboard.writeText(key).then(() => toast("Key copied!", "success"));
}

// -- Hosts -----------------------------------------------------------------
async function viewHosts() {
    setContent(loading());
    const hosts = await API.get("/api/hosts");

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, "Host Management"),
        h("p", {}, "Add and manage Proxmox VE hosts."),
    ]));

    // Add host form
    const formCard = h("div", { className: "card" });
    formCard.appendChild(h("div", { className: "card-header" }, "Add New Host"));
    const formBody = h("div", { className: "card-body" });
    formBody.innerHTML = `
        <div class="form-row">
            <div class="form-group"><label>Name</label><input class="form-control" id="host-name" placeholder="pve-node-1"></div>
            <div class="form-group"><label>Address</label><input class="form-control" id="host-addr" placeholder="192.168.1.10"></div>
            <div class="form-group"><label>Port</label><input class="form-control" id="host-port" value="22" type="number"></div>
            <div class="form-group"><label>User</label><input class="form-control" id="host-user" value="root"></div>
        </div>
        <button class="btn btn-primary" id="add-host-btn" style="margin-top:8px">Add Host</button>
    `;
    formCard.appendChild(formBody);
    container.appendChild(formCard);

    // Hosts table
    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, `Hosts (${hosts.length})`));
    if (hosts.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, [
            h("div", { className: "icon" }, "\u{1F5A5}"),
            h("p", {}, "No hosts added yet."),
        ]));
    } else {
        const table = h("table");
        table.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, "Name"), h("th", {}, "Address"), h("th", {}, "Port"),
            h("th", {}, "User"), h("th", {}, "Status"), h("th", {}, "Actions"),
        ])));
        const tbody = h("tbody");
        for (const host of hosts) {
            const tr = h("tr");
            tr.appendChild(h("td", {}, host.name));
            tr.appendChild(h("td", {}, host.address));
            tr.appendChild(h("td", {}, String(host.port)));
            tr.appendChild(h("td", {}, host.user));
            const statusTd = h("td");
            statusTd.appendChild(h("span", { className: "badge badge-stopped", id: `status-${host.address}` }, "Unknown"));
            tr.appendChild(statusTd);
            const actionsTd = h("td");
            const btnGroup = h("div", { className: "btn-group" });
            btnGroup.appendChild(h("button", {
                className: "btn btn-sm btn-success",
                onClick: () => testHost(host.address),
            }, "Test"));
            btnGroup.appendChild(h("button", {
                className: "btn btn-sm btn-danger",
                onClick: () => deleteHost(host.address),
            }, "Remove"));
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
    if (!name || !addr) { toast("Name and address are required", "error"); return; }
    const r = await API.post("/api/hosts", { name, address: addr, port, user });
    toast(r.message, r.success ? "success" : "error");
    if (r.success) {
        loadHostSelector();
        viewHosts();
    }
}

async function testHost(addr) {
    const el = document.getElementById(`status-${addr}`);
    if (el) { el.textContent = "Testing..."; el.className = "badge badge-stopped"; }
    const r = await API.post("/api/hosts/test", { address: addr });
    if (el) {
        el.textContent = r.success ? "Online" : "Offline";
        el.className = `badge ${r.success ? "badge-online" : "badge-offline"}`;
    }
    toast(r.message, r.success ? "success" : "error");
}

async function deleteHost(addr) {
    if (!confirm(`Remove host ${addr}?`)) return;
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
        h("h2", {}, "ZFS Pools"),
        h("p", {}, `Pools on ${currentHost}`),
    ]));

    if (pools.length === 0) {
        container.appendChild(h("div", { className: "card" }, h("div", { className: "empty-state" }, "No ZFS pools found.")));
    }

    // Pool overview
    const statsGrid = h("div", { className: "grid grid-4" });
    for (const pool of pools) {
        const card = h("div", { className: "stat-card", style: "cursor:pointer", onClick: () => showPoolDetail(pool.name) });
        card.appendChild(h("div", { className: "stat-label" }, pool.name));
        card.appendChild(h("div", { style: "display:flex;align-items:center;gap:8px;margin-top:6px" }, [
            healthBadge(pool.health),
            h("span", { className: "badge badge-stopped", id: `upgrade-badge-${pool.name}`, style: "display:none" }, "Upgrade"),
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
    tableCard.appendChild(h("div", { className: "card-header" }, "All Pools"));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, "Name"), h("th", {}, "Size"), h("th", {}, "Alloc"),
        h("th", {}, "Free"), h("th", {}, "Frag"), h("th", {}, "Cap"),
        h("th", {}, "Health"), h("th", {}, "Actions"),
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
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showPoolDetail(pool.name) }, "Details"));
        bg.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => scrubPool(pool.name) }, "Scrub"));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showPoolHistory(pool.name) }, "History"));
        const upgradeBtn = h("button", {
            className: "btn btn-sm",
            id: `upgrade-btn-${pool.name}`,
            onClick: () => upgradePool(pool.name),
            disabled: "true",
        }, "Upgrade");
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

    // Check upgrade status for each pool asynchronously
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
        <h4 style="margin-bottom:8px">Status</h4>
        <pre class="output">${escapeHtml(status.stdout || status.stderr || "No data")}</pre>
        <h4 style="margin:16px 0 8px">IO Stats</h4>
        <pre class="output">${escapeHtml(iostat.stdout || iostat.stderr || "No data")}</pre>
    `);
}

async function scrubPool(pool) {
    if (!confirm(`Start scrub on ${pool}?`)) return;
    const r = await API.post("/api/pools/scrub", { host: currentHost, pool });
    toast(r.success ? "Scrub started" : (r.stderr || "Scrub failed"), r.success ? "success" : "error");
}

async function showPoolHistory(pool) {
    const r = await API.get(`/api/pools/history?host=${currentHost}&pool=${pool}`);
    openModal(`History: ${pool}`, `<pre class="output">${escapeHtml(r.stdout || r.stderr || "No data")}</pre>`);
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
            btn.title = "Upgrade available! Click to upgrade.";
        }
        if (badge) {
            badge.className = "badge badge-online";
            badge.style.display = "inline-block";
        }
    } else {
        if (btn) {
            btn.className = "btn btn-sm";
            btn.style.opacity = "0.4";
            btn.title = "Already up to date";
        }
    }
}

async function upgradePool(pool) {
    if (!confirm(`Upgrade pool "${pool}" to enable all available ZFS features?\n\nNote: This is irreversible and may prevent importing on older ZFS versions.`)) return;
    const r = await API.post("/api/pools/upgrade", { host: currentHost, pool });
    if (r.success) {
        toast(`Pool ${pool} upgraded successfully`, "success");
        openModal(`Upgrade: ${pool}`, `<pre class="output">${escapeHtml(r.stdout || "Upgrade completed")}</pre>`);
    } else {
        toast(r.stderr || "Upgrade failed", "error");
        openModal(`Upgrade failed: ${pool}`, `<pre class="output">${escapeHtml(r.stderr || r.stdout || "Unknown error")}</pre>`);
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
        h("h2", {}, "ZFS Datasets"),
        h("p", {}, `Datasets on ${currentHost}`),
    ]));

    // Create dataset button
    const headerActions = h("div", { style: "margin-bottom:16px" });
    headerActions.appendChild(h("button", { className: "btn btn-primary", onClick: showCreateDatasetForm }, "Create Dataset"));
    container.appendChild(headerActions);

    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, `Datasets (${datasets.length})`));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}, "Used"),
        h("th", {}, "Avail"), h("th", {}, "Refer"), h("th", {}, "Compress"),
        h("th", {}, "Ratio"), h("th", {}, "Actions"),
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
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => showDatasetProps(ds.name) }, "Properties"));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => createSnapshotForDs(ds.name) }, "Snapshot"));
        bg.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: () => destroyDataset(ds.name) }, "Destroy"));
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
    openModal(`Properties: ${ds}`, `<pre class="output">${escapeHtml(r.stdout || r.stderr || "No data")}</pre>`);
}

function showCreateDatasetForm() {
    openModal("Create Dataset", `
        <div class="form-group"><label>Dataset Name</label><input class="form-control" id="new-ds-name" placeholder="rpool/data/new-dataset"></div>
        <div class="form-group"><label>Compression (optional)</label><input class="form-control" id="new-ds-compress" placeholder="lz4"></div>
    `, async () => {
        const name = document.getElementById("new-ds-name").value.trim();
        if (!name) { toast("Name required", "error"); return; }
        const compress = document.getElementById("new-ds-compress").value.trim();
        const opts = compress ? { compression: compress } : null;
        const r = await API.post("/api/datasets/create", { host: currentHost, name, options: opts });
        toast(r.success ? "Dataset created" : (r.stderr || "Failed"), r.success ? "success" : "error");
        closeModal();
        viewDatasets();
    });
}

async function destroyDataset(name) {
    if (!confirm(`DESTROY dataset ${name}? This cannot be undone!`)) return;
    if (!confirm(`Are you REALLY sure? Type the dataset name below is not possible in this UI, so this is your final chance to cancel.`)) return;
    const r = await API.post("/api/datasets/destroy", { host: currentHost, name, recursive: false });
    toast(r.success ? "Dataset destroyed" : (r.stderr || "Failed"), r.success ? "success" : "error");
    viewDatasets();
}

async function createSnapshotForDs(ds) {
    const snapName = prompt("Snapshot name:", `manual-${Math.floor(Date.now() / 1000)}`);
    if (!snapName) return;
    const r = await API.post("/api/snapshots/create", { host: currentHost, dataset: ds, name: snapName });
    toast(r.success ? "Snapshot created" : (r.stderr || "Failed"), r.success ? "success" : "error");
}

// -- Snapshots -------------------------------------------------------------
let _allSnapshots = [];

async function viewSnapshots() {
    if (!requireHost()) return;
    setContent(loading());
    _allSnapshots = await API.get(`/api/snapshots?host=${currentHost}`);

    const container = h("div");
    container.appendChild(h("div", { className: "page-header" }, [
        h("h2", {}, "ZFS Snapshots"),
        h("p", {}, `All snapshots on ${currentHost} (newest first)`),
    ]));

    // Stats
    const statsGrid = h("div", { className: "grid grid-3", style: "margin-bottom:16px" });
    statsGrid.appendChild(makeStatCard("Total Snapshots", _allSnapshots.length, ""));
    const datasets = [...new Set(_allSnapshots.map(s => s.dataset))];
    statsGrid.appendChild(makeStatCard("Datasets", datasets.length, ""));
    const autoSnaps = _allSnapshots.filter(s => s.snapshot.startsWith("zfs-auto-snap") || s.snapshot.startsWith("autosnap"));
    statsGrid.appendChild(makeStatCard("Auto-Snapshots", autoSnaps.length, ""));
    container.appendChild(statsGrid);

    // Filter + View toggle
    const filterCard = h("div", { className: "card" });
    const filterBody = h("div", { className: "card-body" });
    const filterRow = h("div", { className: "form-row" });
    // Dataset filter
    const dsGroup = h("div", { className: "form-group" });
    dsGroup.appendChild(h("label", {}, "Filter by Dataset"));
    const sel = h("select", { className: "form-control", id: "snap-filter-ds" });
    sel.appendChild(h("option", { value: "" }, "All Datasets"));
    datasets.forEach(d => sel.appendChild(h("option", { value: d }, d)));
    sel.addEventListener("change", applySnapshotFilter);
    dsGroup.appendChild(sel);
    filterRow.appendChild(dsGroup);
    // View toggle
    const viewGroup = h("div", { className: "form-group" });
    viewGroup.appendChild(h("label", {}, "View"));
    const viewSel = h("select", { className: "form-control", id: "snap-view-mode" });
    viewSel.appendChild(h("option", { value: "timeline" }, "Timeline"));
    viewSel.appendChild(h("option", { value: "table" }, "Table"));
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
    const mode = document.getElementById("snap-view-mode").value;
    const filtered = ds ? _allSnapshots.filter(s => s.dataset === ds) : _allSnapshots;

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
        container.innerHTML = '<div class="card"><div class="empty-state">No snapshots found.</div></div>';
        return;
    }

    // Group by dataset
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
            h("span", { style: "font-size:12px;color:var(--text-secondary)" }, `${snaps.length} snapshots`),
        ]));

        const body = h("div", { className: "card-body", style: "padding:16px 16px 8px" });
        const timeline = h("div", { className: "snapshot-timeline" });

        for (let i = 0; i < snaps.length; i++) {
            const snap = snaps[i];
            const isAuto = snap.snapshot.startsWith("zfs-auto-snap") || snap.snapshot.startsWith("autosnap");
            const isFirst = i === 0;

            const node = h("div", { className: `tl-node ${isFirst ? "tl-node-latest" : ""} ${isAuto ? "tl-node-auto" : "tl-node-manual"}` });

            // Dot
            node.appendChild(h("div", { className: `tl-dot ${isFirst ? "tl-dot-latest" : isAuto ? "tl-dot-auto" : "tl-dot-manual"}` }));

            // Content
            const content = h("div", { className: "tl-content" });
            const topRow = h("div", { className: "tl-top-row" });
            topRow.appendChild(h("strong", { className: "tl-snap-name" }, snap.snapshot));
            topRow.appendChild(h("span", { className: "tl-date" }, snap.creation));
            content.appendChild(topRow);

            const metaRow = h("div", { className: "tl-meta" });
            metaRow.appendChild(h("span", {}, `Used: ${snap.used}`));
            metaRow.appendChild(h("span", {}, `Refer: ${snap.refer}`));
            if (isAuto) metaRow.appendChild(h("span", { className: "badge badge-stopped", style: "font-size:9px" }, "auto"));
            content.appendChild(metaRow);

            // Actions
            const actions = h("div", { className: "tl-actions" });
            actions.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => rollbackSnap(snap) }, "Rollback"));
            actions.appendChild(h("button", { className: "btn btn-sm", onClick: () => cloneSnap(snap) }, "Clone"));
            if (!isVolume) {
                actions.appendChild(h("button", { className: "btn btn-sm", onClick: () => diffSnap(snap) }, "Diff"));
            }
            actions.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: () => destroySnap(snap) }, "Delete"));
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
    tableCard.appendChild(h("div", { className: "card-header" }, `Snapshots (${snapshots.length})`));

    if (snapshots.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, "No snapshots found."));
        return;
    }

    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, "Dataset"), h("th", {}, "Snapshot"), h("th", {}, "Type"),
        h("th", {}, "Used"), h("th", {}, "Refer"), h("th", {}, "Created"), h("th", {}, "Actions"),
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
        bg.appendChild(h("button", { className: "btn btn-sm btn-warning", onClick: () => rollbackSnap(snap) }, "Rollback"));
        bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => cloneSnap(snap) }, "Clone"));
        if (!isVolume) {
            bg.appendChild(h("button", { className: "btn btn-sm", onClick: () => diffSnap(snap) }, "Diff"));
        }
        bg.appendChild(h("button", { className: "btn btn-sm btn-danger", onClick: () => destroySnap(snap) }, "Delete"));
        actTd.appendChild(bg);
        tr.appendChild(actTd);
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    tableCard.appendChild(table);
}

// -- Snapshot Actions ------------------------------------------------------
function _detectGuest(snap) {
    // Detect VMID from dataset name like rpool/data/vm-120-disk-0 or rpool/data/subvol-100-disk-0
    const m = snap.dataset.match(/\/(vm|subvol)-(\d+)-disk-/);
    if (m) return { vmid: m[2], vm_type: m[1] === "subvol" ? "lxc" : "qemu" };
    return null;
}

async function rollbackSnap(snap) {
    const guest = _detectGuest(snap);
    let msg = `ROLLBACK to ${snap.full_name}?\nThis will revert the dataset to this snapshot state!`;
    if (guest) {
        msg += `\n\nDetected ${guest.vm_type === "qemu" ? "VM" : "LXC"} ${guest.vmid} — it will be stopped before rollback and restarted afterwards.`;
    }
    if (!confirm(msg)) return;
    const destroyRecent = confirm("Destroy more recent snapshots? (Required if newer snapshots exist)");

    const payload = {
        host: currentHost, snapshot: snap.full_name,
        force: true, destroy_recent: destroyRecent,
    };
    if (guest) {
        payload.stop_guest = true;
        payload.vmid = guest.vmid;
        payload.vm_type = guest.vm_type;
    }

    toast("Performing rollback...", "info");
    const r = await API.post("/api/snapshots/rollback", payload);
    if (r.success) {
        let resultMsg = "Rollback completed";
        if (r.guest_actions?.stopped) resultMsg += " (guest stopped";
        if (r.guest_actions?.started) resultMsg += " & restarted)";
        else if (r.guest_actions?.stopped) resultMsg += ", restart failed!)";
        toast(resultMsg, "success");
    } else {
        toast(r.stderr || "Rollback failed", "error");
    }
    viewSnapshots();
}

async function cloneSnap(snap) {
    const name = prompt("Clone dataset name:", snap.dataset + "-clone");
    if (!name) return;
    const r = await API.post("/api/snapshots/clone", { host: currentHost, snapshot: snap.full_name, clone_name: name });
    toast(r.success ? "Clone created" : (r.stderr || "Clone failed"), r.success ? "success" : "error");
}

async function diffSnap(snap) {
    openModal(`Diff: ${snap.snapshot}`, '<div class="loading-placeholder"><span class="spinner"></span> Loading diff...</div>');
    const r = await API.get(`/api/snapshots/diff?host=${currentHost}&snapshot1=${encodeURIComponent(snap.full_name)}`);
    const body = document.getElementById("modal-body");
    if (body) {
        if (r.success) {
            body.innerHTML = `<pre class="output">${escapeHtml(r.stdout || "(No changes)")}</pre>`;
        } else {
            body.innerHTML = `<div style="color:var(--danger);margin-bottom:12px;font-weight:600">Diff failed</div><pre class="output">${escapeHtml(r.stderr || "Unknown error")}</pre>`;
        }
    }
}

async function destroySnap(snap) {
    if (!confirm(`Delete snapshot ${snap.full_name}?`)) return;
    const r = await API.post("/api/snapshots/destroy", { host: currentHost, snapshot: snap.full_name });
    toast(r.success ? "Snapshot deleted" : (r.stderr || "Delete failed"), r.success ? "success" : "error");
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
        h("h2", {}, "VMs & Containers"),
        h("p", {}, `Proxmox guests on ${currentHost}`),
    ]));

    // Stats
    const statsGrid = h("div", { className: "grid grid-3", style: "margin-bottom:16px" });
    statsGrid.appendChild(makeStatCard("VMs", (guests.vms || []).length, ""));
    statsGrid.appendChild(makeStatCard("Containers", (guests.cts || []).length, ""));
    statsGrid.appendChild(makeStatCard("Total", all.length, ""));
    container.appendChild(statsGrid);

    // Guest table
    const tableCard = h("div", { className: "card" });
    tableCard.appendChild(h("div", { className: "card-header" }, "All Guests"));
    if (all.length === 0) {
        tableCard.appendChild(h("div", { className: "empty-state" }, "No VMs or containers found."));
    } else {
        const table = h("table");
        table.appendChild(h("thead", {}, h("tr", {}, [
            h("th", {}, "VMID"), h("th", {}, "Name"), h("th", {}, "Type"),
            h("th", {}, "Status"), h("th", {}, "Actions"),
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
            }, "Snapshots"));
            bg.appendChild(h("button", {
                className: "btn btn-sm",
                onClick: () => createGuestSnapshot(g, pools),
            }, "New Snapshot"));
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

    let html = `<p style="margin-bottom:12px">ZFS snapshots for ${guest.type.toUpperCase()} ${guest.vmid} (${guest.name})</p>`;
    if (snaps.length === 0) {
        html += '<p style="color:var(--text-secondary)">No snapshots found for this guest.</p>';
    } else {
        html += '<table><thead><tr><th>Snapshot</th><th>Used</th><th>Refer</th><th>Created</th><th>Actions</th></tr></thead><tbody>';
        for (const s of snaps) {
            html += `<tr>
                <td><strong>${escapeHtml(s.snapshot)}</strong><br><span style="font-size:11px;color:var(--text-secondary)">${escapeHtml(s.dataset)}</span></td>
                <td>${s.used}</td><td>${s.refer}</td><td style="font-size:12px">${escapeHtml(s.creation)}</td>
                <td>
                    <div class="btn-group">
                        <button class="btn btn-sm btn-warning" onclick="rollbackGuestSnap('${escapeHtml(s.full_name)}')">Rollback</button>
                        <button class="btn btn-sm btn-danger" onclick="destroyGuestSnap('${escapeHtml(s.full_name)}')">Delete</button>
                    </div>
                </td>
            </tr>`;
        }
        html += '</tbody></table>';
    }
    openModal(`Snapshots: ${guest.type.toUpperCase()} ${guest.vmid}`, html);
}

async function createGuestSnapshot(guest, pools) {
    const poolName = pools.length > 0 ? pools[0].name : "rpool";
    const prefix = guest.type === "lxc" ? "subvol" : "vm";
    const dataset = `${poolName}/data/${prefix}-${guest.vmid}-disk-0`;
    const snapName = prompt("Snapshot name:", `manual-${Math.floor(Date.now() / 1000)}`);
    if (!snapName) return;
    const r = await API.post("/api/snapshots/create", { host: currentHost, dataset, name: snapName });
    toast(r.success ? "Snapshot created" : (r.stderr || "Failed – check dataset path"), r.success ? "success" : "error");
}

// These need to be global for inline onclick
window.rollbackGuestSnap = async function(fullName) {
    const m = fullName.match(/\/(vm|subvol)-(\d+)-disk-/);
    const guest = m ? { vmid: m[2], vm_type: m[1] === "subvol" ? "lxc" : "qemu" } : null;
    let msg = `ROLLBACK to ${fullName}?`;
    if (guest) msg += `\n\nThe ${guest.vm_type === "qemu" ? "VM" : "LXC"} ${guest.vmid} will be stopped before rollback and restarted afterwards.`;
    if (!confirm(msg)) return;
    const payload = { host: currentHost, snapshot: fullName, force: true, destroy_recent: true };
    if (guest) { payload.stop_guest = true; payload.vmid = guest.vmid; payload.vm_type = guest.vm_type; }
    toast("Performing rollback...", "info");
    const r = await API.post("/api/snapshots/rollback", payload);
    toast(r.success ? "Rollback completed" : (r.stderr || "Rollback failed"), r.success ? "success" : "error");
    closeModal();
};

window.destroyGuestSnap = async function(fullName) {
    if (!confirm(`Delete snapshot ${fullName}?`)) return;
    const r = await API.post("/api/snapshots/destroy", { host: currentHost, snapshot: fullName });
    toast(r.success ? "Deleted" : (r.stderr || "Failed"), r.success ? "success" : "error");
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

    // Status card
    const statusCard = h("div", { className: "card" });
    statusCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, "Status"),
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

    // Dataset config
    const dsCard = h("div", { className: "card" });
    dsCard.appendChild(h("div", { className: "card-header" }, "Dataset Auto-Snapshot Settings"));
    const table = h("table");
    table.appendChild(h("thead", {}, h("tr", {}, [
        h("th", {}, "Dataset"),
        h("th", { style: "text-align:center;width:160px" }, "Auto-Snapshot"),
        h("th", { style: "text-align:right" }, "Actions"),
    ])));
    const tbody = h("tbody", { id: "autosnap-tbody" });
    table.appendChild(tbody);
    dsCard.appendChild(table);
    container.appendChild(dsCard);
    setContent(container);

    // Load auto-snapshot properties for each dataset
    const tbodyEl = document.getElementById("autosnap-tbody");
    for (const ds of datasets) {
        const prop = await API.get(`/api/auto-snapshot/property?host=${currentHost}&dataset=${ds.name}`);
        const val = prop.value;
        const source = prop.source || "";
        // Determine effective state:
        // "true" = explicitly enabled or inherited as true
        // "false" = explicitly disabled or inherited as false
        // "-" or anything else = not set at all
        const isActive = val === "true";
        const isInactive = val === "false";
        const isInherited = source.startsWith("inherited") || source === "default";
        const isNotSet = val === "-" || val === "" || (!isActive && !isInactive);

        const tr = h("tr");
        tr.appendChild(h("td", {}, ds.name));

        // Status column with icon
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

        // Actions column
        const actTd = h("td", { style: "text-align:right" });
        const bg = h("div", { className: "btn-group", style: "justify-content:flex-end" });
        const enableBtn = h("button", {
            className: "btn btn-sm btn-success",
        }, "Enable");
        const disableBtn = h("button", {
            className: "btn btn-sm btn-danger",
        }, "Disable");

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
            // Not set — both clickable
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
    toast(r.success ? `Auto-snapshot ${enabled ? "enabled" : "disabled"} for ${ds}` : (r.stderr || "Failed"),
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
        h("h2", {}, "Health & Monitoring"),
        h("p", {}, `ZFS health information for ${currentHost}`),
    ]));

    // ARC stats
    const arcCard = h("div", { className: "card" });
    arcCard.appendChild(h("div", { className: "card-header" }, "ARC (Adaptive Replacement Cache) Statistics"));
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
        arcBody.appendChild(h("pre", { className: "output" }, arc.stderr || "No ARC stats available"));
    }
    arcCard.appendChild(arcBody);
    container.appendChild(arcCard);

    // Events
    const evCard = h("div", { className: "card" });
    evCard.appendChild(h("div", { className: "card-header" }, "Recent ZFS Events"));
    evCard.appendChild(h("div", { className: "card-body" }, [
        h("pre", { className: "output" }, events.stdout || events.stderr || "No events"),
    ]));
    container.appendChild(evCard);

    // SMART check per pool
    const pools = await API.get(`/api/pools?host=${currentHost}`);
    for (const pool of pools) {
        const smart = await API.get(`/api/health/smart?host=${currentHost}&pool=${pool.name}`);
        const smartCard = h("div", { className: "card" });
        smartCard.appendChild(h("div", { className: "card-header" }, `SMART Status: ${pool.name}`));
        const smartBody = h("div", { className: "card-body" });
        if (smart.disks) {
            for (const [disk, status] of Object.entries(smart.disks)) {
                const ok = status.toLowerCase().includes("passed") || status.toLowerCase().includes("ok");
                smartBody.appendChild(h("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:6px" }, [
                    h("code", {}, disk),
                    h("span", { className: `badge ${ok ? "badge-online" : "badge-offline"}` }, status || "Unknown"),
                ]));
            }
        } else {
            smartBody.appendChild(h("p", { style: "color:var(--text-secondary)" }, smart.stderr || "Could not retrieve SMART data"));
        }
        smartCard.appendChild(smartBody);
        container.appendChild(smartCard);
    }

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
        h("h2", {}, "Notifications"),
        h("p", {}, "Configure Telegram and Gotify notifications for ZFS events."),
    ]));

    // --- Telegram Card ---
    const tgCard = h("div", { className: "card" });
    tgCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, "Telegram"),
        h("span", {
            className: `badge ${config.telegram?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.telegram?.enabled ? "Enabled" : "Disabled"),
    ]));
    const tgBody = h("div", { className: "card-body" });
    tgBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="tg-enabled" ${config.telegram?.enabled ? "checked" : ""}>
                Enable Telegram notifications
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Bot Token</label>
                <input class="form-control" id="tg-token" placeholder="123456:ABC-DEF..." value="${escapeAttr(config.telegram?.bot_token || "")}">
            </div>
            <div class="form-group">
                <label>Chat ID</label>
                <input class="form-control" id="tg-chat-id" placeholder="-1001234567890" value="${escapeAttr(config.telegram?.chat_id || "")}">
            </div>
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="tg-test-btn">Send Test</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            Create a bot via <strong>@BotFather</strong> on Telegram. Use <strong>@userinfobot</strong> or
            <strong>@getidsbot</strong> to find your Chat ID. For groups, add the bot to the group and use the group Chat ID (starts with <code>-100</code>).
        </p>
    `;
    tgCard.appendChild(tgBody);
    container.appendChild(tgCard);

    // --- Gotify Card ---
    const gtCard = h("div", { className: "card" });
    gtCard.appendChild(h("div", { className: "card-header" }, [
        h("span", {}, "Gotify"),
        h("span", {
            className: `badge ${config.gotify?.enabled ? "badge-online" : "badge-offline"}`,
        }, config.gotify?.enabled ? "Enabled" : "Disabled"),
    ]));
    const gtBody = h("div", { className: "card-body" });
    gtBody.innerHTML = `
        <div class="form-group">
            <label class="checkbox-label">
                <input type="checkbox" id="gt-enabled" ${config.gotify?.enabled ? "checked" : ""}>
                Enable Gotify notifications
            </label>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Server URL</label>
                <input class="form-control" id="gt-url" placeholder="https://gotify.example.com" value="${escapeAttr(config.gotify?.url || "")}">
            </div>
            <div class="form-group">
                <label>App Token</label>
                <input class="form-control" id="gt-token" placeholder="Axxxxxxxxx" value="${escapeAttr(config.gotify?.token || "")}">
            </div>
        </div>
        <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-sm btn-success" id="gt-test-btn">Send Test</button>
        </div>
        <p style="margin-top:10px;font-size:12px;color:var(--text-secondary)">
            Create an application in Gotify and use its token here. The Server URL should be the base URL without trailing slash.
        </p>
    `;
    gtCard.appendChild(gtBody);
    container.appendChild(gtCard);

    // --- Event Configuration ---
    const evCard = h("div", { className: "card" });
    evCard.appendChild(h("div", { className: "card-header" }, "Event Configuration"));
    const evBody = h("div", { className: "card-body" });

    const eventLabels = {
        scrub_started: "Scrub Started",
        scrub_finished: "Scrub Finished",
        rollback: "Snapshot Rollback",
        snapshot_created: "Snapshot Created",
        snapshot_deleted: "Snapshot Deleted",
        pool_error: "Pool Error / Degraded",
        health_warning: "Health Warning",
        host_offline: "Host Offline",
        auto_snapshot: "Auto-Snapshot Events",
    };

    const evGrid = h("div", { className: "grid grid-3" });
    for (const [key, label] of Object.entries(eventLabels)) {
        const checked = config.events?.[key] !== false;
        const item = h("div", { style: "padding:6px 0" });
        item.innerHTML = `
            <label class="checkbox-label">
                <input type="checkbox" class="ev-checkbox" data-event="${key}" ${checked ? "checked" : ""}>
                ${label}
            </label>
        `;
        evGrid.appendChild(item);
    }
    evBody.appendChild(evGrid);
    evCard.appendChild(evBody);
    container.appendChild(evCard);

    // --- Save Button ---
    const saveBar = h("div", { style: "margin-top:16px;display:flex;gap:8px" });
    saveBar.appendChild(h("button", { className: "btn btn-primary", id: "notify-save-btn" }, "Save Configuration"));
    container.appendChild(saveBar);

    setContent(container);

    // --- Wire up buttons ---
    document.getElementById("tg-test-btn").addEventListener("click", async () => {
        const token = document.getElementById("tg-token").value.trim();
        const chatId = document.getElementById("tg-chat-id").value.trim();
        if (!token || !chatId) { toast("Bot Token and Chat ID required", "error"); return; }
        const r = await API.post("/api/notifications/test/telegram", { bot_token: token, chat_id: chatId });
        toast(r.success ? "Telegram test sent!" : `Telegram failed: ${r.detail || "Unknown error"}`,
            r.success ? "success" : "error");
    });

    document.getElementById("gt-test-btn").addEventListener("click", async () => {
        const url = document.getElementById("gt-url").value.trim();
        const token = document.getElementById("gt-token").value.trim();
        if (!url || !token) { toast("Server URL and Token required", "error"); return; }
        const r = await API.post("/api/notifications/test/gotify", { url, token });
        toast(r.success ? "Gotify test sent!" : `Gotify failed: ${r.detail || "Unknown error"}`,
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
            events,
        };
        const r = await API.post("/api/notifications/config", newConfig);
        toast(r.message || "Saved", r.success ? "success" : "error");
        viewNotifications();
    });
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
    footer.appendChild(h("button", { className: "btn", onClick: closeModal }, "Close"));
    if (onConfirm) {
        footer.appendChild(h("button", { className: "btn btn-primary", onClick: onConfirm }, "Confirm"));
    }
    overlay.classList.add("active");
}

function closeModal() {
    document.getElementById("modal-overlay").classList.remove("active");
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
    sel.innerHTML = '<option value="">-- Select Host --</option>';
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
    loadHostSelector();

    document.getElementById("host-select").addEventListener("change", (e) => {
        currentHost = e.target.value || null;
        if (currentView !== "home" && currentView !== "hosts") renderView();
    });

    document.querySelectorAll(".nav-item").forEach(el => {
        el.addEventListener("click", () => navigate(el.dataset.view));
    });

    document.getElementById("modal-overlay").addEventListener("click", (e) => {
        if (e.target === e.currentTarget) closeModal();
    });

    navigate("home");
});
