// Login form. A file of its own so the page needs no inline script and the
// Content-Security-Policy can say script-src 'self'.
document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("login-btn");
    const errEl = document.getElementById("login-error");
    errEl.textContent = "";
    btn.disabled = true;
    btn.textContent = "Logging in...";
    try {
        const r = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: document.getElementById("username").value,
                password: document.getElementById("password").value,
            }),
        });
        const data = await r.json();
        if (data.success) {
            if (data.csrf_token) sessionStorage.setItem("csrf_token", data.csrf_token);
            window.location.href = "/";
        } else {
            errEl.textContent = data.error || "Login failed";
            btn.disabled = false;
            btn.textContent = "Login";
        }
    } catch (err) {
        errEl.textContent = "Connection error";
        btn.disabled = false;
        btn.textContent = "Login";
    }
});
